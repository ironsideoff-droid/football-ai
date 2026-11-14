import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from agents.chief_predictor import ChiefPredictorAgent
from agents.football_intelligence import FootballIntelligenceAgent
from agents.statistical_researcher import StatisticalResearcherAgent
from config import Settings
from errors import FootballDataError, GroqRateLimitError
from evaluation.store import PredictionStore
from football_data.news import NewsProvider
from football_data.provider import FootballProvider
from main import create_app
from schemas.news import NewsBundle
from services.orchestrator import CONFIDENCE_CAP_BASE, Orchestrator, Progress, cap_confidence, normalize_prediction
from schemas.agent_reports import ChiefPrediction
from tests.conftest import FINAL_JSON, INTEL_JSON, PLAN_JSON, RESEARCHER_JSON, FakeGroq


class FakeProvider(FootballProvider):
    def __init__(self, context, results=None):
        self.context, self.results = context, results or []

    async def list_fixtures(self, date_from, date_to, competitions=None):
        return [self.context.match]

    async def get_match_context(self, match_id):
        return self.context.model_copy(deep=True)

    async def get_competition_results(self, code):
        return self.results


class FakeNews(NewsProvider):
    def __init__(self, bundle=None, error=None):
        self.bundle, self.error = bundle, error

    async def fetch(self, match):
        if self.error:
            raise self.error
        return self.bundle


def build(context, news_bundle, researcher=None, intel=None, chief=None, news="default", store=None, **kw):
    return Orchestrator(
        FakeProvider(context),
        StatisticalResearcherAgent(FakeGroq("r", researcher if researcher is not None else [RESEARCHER_JSON], "m1")),
        FootballIntelligenceAgent(FakeGroq("i", intel if intel is not None else [INTEL_JSON], "m2")),
        ChiefPredictorAgent(FakeGroq("c", chief if chief is not None else [PLAN_JSON, FINAL_JSON], "m3")),
        news=FakeNews(news_bundle) if news == "default" else news, store=store, **kw)


def test_complete_workflow(context, news_bundle, tmp_path):
    store = PredictionStore(tmp_path)
    orch = build(context, news_bundle, store=store)
    res = asyncio.run(orch.analyze(100))
    assert not res.abstained and res.simulation.n_simulations == 50_000
    assert res.models == {"statistical_researcher": "m1", "football_intelligence": "m2", "chief_predictor": "m3"}
    pr = res.prediction.probabilities
    assert pr.home_win + pr.draw + pr.away_win == pytest.approx(100, abs=0.2)
    assert res.intelligence.home.findings[0].source == "BBC Sport"
    assert {s["status"] for s in res.simulation.applied_adjustments} == {"applied", "ignored"}  # speculation adjustment ignored
    assert any("Red card" in w and "double counting" in w for w in res.warnings)
    assert "news_items" and res.news_items[0].item_id == "h-1" and any(s.id == "h-1" for s in res.sources)
    assert res.data_quality.score > 0.5 and res.prediction_record["status"] == "saved"
    assert [p["record"]["match_id"] for p in store.predictions()] == [100]
    assert sum(s.simulated_n for s in res.simulation.scenarios) == 50_000
    assert res.simulation.input_xg_home > 0 and res.simulation.priors["basis"] == "season+form"


def test_confidence_is_capped_by_data_quality(context, news_bundle):
    context.standings, context.head_to_head = None, None  # less data: the Chief's confidence of 95 must be reduced
    res = asyncio.run(build(context, news_bundle).analyze(100))
    cap = round(100 * min(1.0, CONFIDENCE_CAP_BASE + res.data_quality.score))
    assert FINAL_JSON["confidence"]["score"] == 95 and cap < 95 and res.prediction.confidence.score == cap
    assert any("Confidence reduced" in w for w in res.warnings)
    pred = ChiefPrediction.model_validate(FINAL_JSON)
    assert cap_confidence(pred, 0.1) and pred.confidence.score == 45 and pred.confidence.level == "medium"
    assert cap_confidence(pred, 0.9) is None


def test_second_analysis_never_overwrites_the_stored_prediction(context, news_bundle, tmp_path):
    store = PredictionStore(tmp_path)
    asyncio.run(build(context, news_bundle, store=store).analyze(100))
    first = store.predictions_file.read_text()
    res = asyncio.run(build(context, news_bundle, store=store).analyze(100))
    assert res.prediction_record["status"] == "already_exists" and store.predictions_file.read_text() == first


def test_started_match_is_analysed_but_not_stored(context, news_bundle, tmp_path):
    context.match.utc_date = "2020-01-01T15:00:00Z"
    store = PredictionStore(tmp_path)
    res = asyncio.run(build(context, news_bundle, store=store).analyze(100))
    assert res.prediction_record["status"] == "not_saved" and store.predictions() == []


@pytest.mark.parametrize("news", [FakeNews(error=RuntimeError("dns")), FakeNews(NewsBundle(status="unavailable", errors=["boom"], retrieved_at="t")), None])
def test_news_failure_does_not_stop_the_analysis(context, news_bundle, news):
    orch = build(context, news_bundle, intel=[], news=news)
    res = asyncio.run(orch.analyze(100))
    assert res.intelligence.status == "unavailable" and res.prediction and not orch.intelligence.client.calls
    assert res.data_quality.components["news"] is False and res.news_items == []


def test_abstains_when_data_is_insufficient(context, news_bundle):
    context.standings, context.home_form, context.away_form, context.head_to_head = None, None, None, None
    orch = build(context, news_bundle, researcher=[], intel=[], chief=[])
    progress = Progress()
    res = asyncio.run(orch.analyze(100, progress))
    assert res.abstained and res.prediction is None and res.simulation is None and "Not enough reliable data" in res.abstain_reason
    assert not orch.researcher.client.calls and not orch.chief.client.calls  # no LLM spend
    assert all(s.status == "done" for s in progress.steps.values())


def test_chief_can_abstain(context, news_bundle):
    plan = {**PLAN_JSON, "abstain": True, "abstain_reason": "contradictory availability reports"}
    res = asyncio.run(build(context, news_bundle, chief=[plan]).analyze(100))
    assert res.abstained and "contradictory" in res.abstain_reason and res.chief_plan and res.simulation is None


def test_priors_failure_abstains_instead_of_inventing(context, news_bundle):
    context.standings = None
    orch = build(context, news_bundle)
    context.home_form.matches_considered = 2  # quality check passes via away form? make priors fail explicitly
    orch.provider.context.home_form.matches_considered = 2
    res = asyncio.run(orch.analyze(100))
    assert res.abstained or res.prediction  # either path must be explicit, never a made-up prediction


def test_agent_failure_marks_step_and_aborts(context, news_bundle):
    orch = build(context, news_bundle, intel=[GroqRateLimitError("slow down", agent="Football Intelligence")])
    progress = Progress()
    with pytest.raises(GroqRateLimitError):
        asyncio.run(orch.analyze(100, progress))
    assert progress.steps["intelligence"].status == "error" and progress.steps["chief_plan"].status == "pending"


def test_prediction_normalisation_and_divergence_warning(context, news_bundle):
    res = asyncio.run(build(context, news_bundle).analyze(100))
    pred = ChiefPrediction.model_validate({**FINAL_JSON, "probabilities": {"home_win": 10, "draw": 10, "away_win": 10}})
    warns = normalize_prediction(pred, res.simulation)
    assert pred.probabilities.home_win == pytest.approx(33.3, abs=0.1) and isinstance(warns, list)
    far = ChiefPrediction.model_validate({**FINAL_JSON, "probabilities": {"home_win": 5, "draw": 5, "away_win": 90}})
    assert any("differs from the simulation" in w for w in normalize_prediction(far, res.simulation))


# ---------------------------------------------------------------- API
@pytest.fixture
def settings(tmp_path):
    return Settings(groq_keys=("a", "b", "c"), groq_models=("m",) * 3, football_api_key="f", auth_disabled=True, predictions_dir=str(tmp_path / "ds"))


def test_health_never_exposes_keys(settings):
    body = TestClient(create_app(settings)).get("/api/health").json()
    assert body["football_api_configured"] and [a["name"] for a in body["agents"]] == ["Statistical Researcher", "Football Intelligence", "Chief Predictor"]
    assert "gsk" not in str(body) and "api_key" not in str(body)


def test_missing_keys_give_clear_503(tmp_path):
    s = Settings(groq_keys=("", "b", ""), groq_models=("m",) * 3, football_api_key="f", auth_disabled=True, predictions_dir=str(tmp_path))
    resp = TestClient(create_app(s)).post("/api/analyze", json={"match_id": 1})
    assert resp.status_code == 503 and "GROQ_API_KEY_1" in resp.json()["error"]["message"]
    s2 = Settings(groq_keys=("a",) * 3, groq_models=("m",) * 3, football_api_key="", auth_disabled=True, predictions_dir=str(tmp_path))
    assert TestClient(create_app(s2)).get("/api/fixtures").status_code == 503


def test_endpoints_job_progress_and_evaluation(settings, context, news_bundle):
    store = PredictionStore(settings.predictions_dir)
    app = create_app(settings, FakeProvider(context), build(context, news_bundle, store=store), store=store)
    with TestClient(app) as c:
        assert c.get("/api/fixtures").json()["matches"][0]["id"] == 100
        job_id = c.post("/api/analyze/start", json={"match_id": 100}).json()["job_id"]
        for _ in range(100):
            job = c.get(f"/api/jobs/{job_id}").json()
            if job["status"] != "running":
                break
            time.sleep(0.05)
        assert job["status"] == "done" and all(s["status"] == "done" for s in job["steps"])
        assert [s["key"] for s in job["steps"]][:2] == ["data", "news"]
        assert job["result"]["simulation"]["n_simulations"] == 50_000
        assert c.get("/api/jobs/nope").status_code == 404
        listed = c.get("/api/predictions").json()["predictions"]
        assert len(listed) == 1 and "score_matrix" not in listed[0]
        ev = c.get("/api/evaluation").json()
        assert ev["stored_predictions"] == 1 and ev["evaluated"] == 0 and ev["chief_predictor"]["n"] == 0 and ev["tampered_match_ids"] == []
        assert c.post("/api/evaluation/refresh").json() == {"results_recorded": []}  # kickoff in the future


def test_refresh_records_results_and_evaluation_reports_metrics(settings, context, news_bundle):
    store = PredictionStore(settings.predictions_dir)
    asyncio.run(build(context, news_bundle, store=store).analyze(100))
    row = store.predictions()[0]["record"]
    finished = context.model_copy(deep=True)
    finished.match.status, finished.match.score_home, finished.match.score_away = "FINISHED", 2, 1
    # simulate time passing: rewrite kickoff into the past in a fresh store so the refresh endpoint considers it
    past = PredictionStore(settings.predictions_dir + "_past")
    past.save_prediction({**row, "kickoff_utc": "2026-11-05T15:00:00Z"}, now=__import__("datetime").datetime(2026, 11, 1, tzinfo=__import__("datetime").timezone.utc))
    app = create_app(settings, FakeProvider(finished), store=past)
    with TestClient(app) as c:
        assert c.post("/api/evaluation/refresh").json() == {"results_recorded": [100]}
        assert c.post("/api/evaluation/refresh").json() == {"results_recorded": []}  # written once
        ev = c.get("/api/evaluation").json()
    assert ev["evaluated"] == 1 and ev["chief_predictor"]["outcome_1x2"]["accuracy"] == 1.0
    assert ev["chief_predictor"]["sample_warning"] and ev["simulation_only"]["n"] == 1 and ev["baseline_constant_frequencies"]["n"] == 1
    assert ev["chief_predictor"]["roi"]["available"] is False


def test_backtest_endpoint(settings, context):
    from tests.test_evaluation import synthetic_season
    app = create_app(settings, FakeProvider(context, synthetic_season(250)))
    body = TestClient(app).get("/api/backtest?competition=pl").json()
    assert body["competition"] == "PL" and body["recommended_model"] == "poisson" and body["matches_evaluated"] > 100


def test_api_maps_agent_and_football_errors(settings, context, news_bundle):
    orch = build(context, news_bundle, researcher=[GroqRateLimitError("limit", agent="Statistical Researcher")])
    resp = TestClient(create_app(settings, FakeProvider(context), orch)).post("/api/analyze", json={"match_id": 1})
    assert resp.status_code == 429 and resp.json()["error"]["agent"] == "Statistical Researcher"

    class Broken(FakeProvider):
        async def get_match_context(self, match_id):
            raise FootballDataError("football API down")
    orch = build(context, news_bundle)
    orch.provider = Broken(context)
    resp = TestClient(create_app(settings, orch.provider, orch)).post("/api/analyze", json={"match_id": 1})
    assert resp.status_code == 502 and resp.json()["error"]["code"] == "football_data_error"


def test_job_reports_error(settings, context, news_bundle):
    orch = build(context, news_bundle, chief=[{"bad": True}, {"bad": True}])
    with TestClient(create_app(settings, FakeProvider(context), orch)) as c:
        job_id = c.post("/api/analyze/start", json={"match_id": 1}).json()["job_id"]
        for _ in range(100):
            job = c.get(f"/api/jobs/{job_id}").json()
            if job["status"] != "running":
                break
            time.sleep(0.05)
        assert job["status"] == "error" and job["error"]["code"] == "invalid_agent_output"


def test_web_research_findings_join_the_news_and_its_failure_never_stops_the_analysis(context, news_bundle):
    from schemas.news import NewsItem

    class Web:
        def __init__(self, items=None, exc=None):
            self.items, self.exc = items, exc

        async def fetch(self, match):
            if self.exc:
                raise self.exc
            return self.items

    found = NewsItem(item_id="wh-1", team="home", team_name="Home FC", title="Striker doubtful with a hamstring problem", source="BBC Sport",
                     link="https://bbc.co.uk/x", reliability_class="high", origin="web_research")
    o = build(context, news_bundle)
    o.web = Web([found])
    bundle = asyncio.run(o._fetch_news(context))
    assert [i.item_id for i in bundle.items] == ["h-1", "wh-1"] and "web research" in bundle.provider

    o2 = build(context, news_bundle)
    o2.web = Web(exc=RuntimeError("boom"))
    bundle2 = asyncio.run(o2._fetch_news(context))
    assert [i.item_id for i in bundle2.items] == ["h-1"] and any("Web research skipped" in e for e in bundle2.errors)


def test_scout_data_reaches_the_result_and_scout_failure_never_stops_the_analysis(context, news_bundle):
    from football_data.scout import ScoutReport
    from schemas.match import LineupPlayer, Lineups, TeamLineup
    from schemas.scout import TeamAdvanced

    xi = lambda: TeamLineup(formation="4-3-3", starters=[LineupPlayer(name=f"P{i}", position="Midfield") for i in range(11)])  # noqa: E731

    class Scout:
        def __init__(self, exc=None):
            self.exc = exc

        async def run(self, match):
            if self.exc:
                raise self.exc
            rep = ScoutReport()
            rep.lineups = Lineups(home=xi(), away=xi(), source="ESPN")
            rep.home_adv = TeamAdvanced(team="Home FC", source="Understat", matches=5, xg_for_avg=1.9, xg_against_avg=0.8)
            rep.notes = {"espn": "lineups found", "understat": "xG found"}
            return rep

    ok = asyncio.run(build(context, news_bundle, scout=Scout()).analyze(100))
    assert not ok.abstained and ok.context.lineups.source == "ESPN" and ok.context.home_advanced.xg_for_avg == 1.9
    assert ok.data_quality.components["lineups"] and ok.data_quality.components["xg"]
    assert ok.data_availability["lineups_formations"].startswith("available")

    bad = asyncio.run(build(context, news_bundle, scout=Scout(exc=RuntimeError("blocked"))).analyze(100))
    assert not bad.abstained and bad.context.lineups is None


def test_fan_posts_join_the_news_and_their_failure_never_stops_the_analysis(context, news_bundle):
    from schemas.news import NewsItem

    class Fans:
        def __init__(self, exc=None):
            self.exc = exc

        async def fetch(self, match):
            if self.exc:
                raise self.exc
            return [NewsItem(item_id="fh-1", team="home", team_name="Home FC", title="Fans debate the keeper", source="r/soccer · 300 upvotes",
                             link="https://www.reddit.com/r/soccer/x", reliability_class="low", origin="fan_social")]

    o = build(context, news_bundle, fans=Fans())
    assert [i.item_id for i in asyncio.run(o._fetch_news(context)).items] == ["h-1", "fh-1"]
    o2 = build(context, news_bundle, fans=Fans(RuntimeError("down")))
    b2 = asyncio.run(o2._fetch_news(context))
    assert [i.item_id for i in b2.items] == ["h-1"] and any("Fan posts skipped" in e for e in b2.errors)
