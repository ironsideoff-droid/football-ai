import time

import pytest
from fastapi.testclient import TestClient

from agents.chief_predictor import ChiefPredictorAgent
from agents.researcher import ResearcherAgent
from agents.tactical_analyst import TacticalAnalystAgent
from config import Settings
from errors import GroqRateLimitError
from football_data.provider import FootballProvider
from main import create_app
from services.orchestrator import Orchestrator
from tests.conftest import FINAL_JSON, PLAN_JSON, RESEARCHER_JSON, TACTICAL_JSON, FakeGroq

import asyncio


class FakeProvider(FootballProvider):
    def __init__(self, context):
        self.context = context

    async def list_fixtures(self, date_from, date_to, competitions=None):
        return [self.context.match]

    async def get_match_context(self, match_id):
        return self.context


def build(context, researcher=None, tactical=None, chief=None, **kw):
    return Orchestrator(
        FakeProvider(context),
        ResearcherAgent(FakeGroq("r", researcher if researcher is not None else [RESEARCHER_JSON], "m1")),
        TacticalAnalystAgent(FakeGroq("t", tactical if tactical is not None else [TACTICAL_JSON], "m2")),
        ChiefPredictorAgent(FakeGroq("c", chief if chief is not None else [PLAN_JSON, FINAL_JSON], "m3")),
        **kw,
    )


def test_complete_workflow(context):
    result = asyncio.run(build(context).analyze(100))
    assert result.simulation.n_simulations == 1000
    assert result.simulation.params_applied.home_xg == 1.65  # chief's parameters drive the simulation
    assert result.models == {"researcher": "m1", "tactical_analyst": "m2", "chief_predictor": "m3"}
    assert result.prediction.probabilities.home_win + result.prediction.probabilities.draw + result.prediction.probabilities.away_win == pytest.approx(100, abs=0.2)


def test_sequential_mode_passes_research_to_tactical(context):
    orch = build(context, pass_research_to_tactical=True)
    asyncio.run(orch.analyze(100))
    assert "researcher_report" in orch.tactical.client.calls[0][1]


def test_failure_marks_step_and_aborts(context):
    from services.orchestrator import Progress
    orch = build(context, tactical=[GroqRateLimitError("slow down", agent="Tactical Analyst")])
    progress = Progress()
    with pytest.raises(GroqRateLimitError):
        asyncio.run(orch.analyze(100, progress))
    assert progress.steps["tactical"].status == "error" and progress.steps["chief_plan"].status == "pending"


@pytest.fixture
def settings():
    return Settings(groq_keys=("a", "b", "c"), groq_models=("m",) * 3, football_api_key="f")


def test_health_reports_configuration_without_exposing_keys(settings):
    body = TestClient(create_app(settings, provider=None)).get("/api/health").json()
    assert body["football_api_configured"] is True and all(a["key_configured"] for a in body["agents"])
    assert "a" not in [a.get("api_key") for a in body["agents"]] and "key" not in str(body).replace("key_configured", "")


def test_missing_keys_give_clear_503():
    s = Settings(groq_keys=("", "b", ""), groq_models=("m",) * 3, football_api_key="f")
    resp = TestClient(create_app(s)).post("/api/analyze", json={"match_id": 1})
    assert resp.status_code == 503 and "GROQ_API_KEY_1" in resp.json()["error"]["message"]
    s2 = Settings(groq_keys=("a",) * 3, groq_models=("m",) * 3, football_api_key="")
    assert TestClient(create_app(s2)).get("/api/fixtures").status_code == 503


def test_endpoints_and_job_progress(settings, context):
    client = TestClient(create_app(settings, FakeProvider(context), build(context)))
    assert client.get("/api/fixtures").json()["matches"][0]["id"] == 100
    assert client.get("/api/matches/100").json()["match"]["id"] == 100
    assert client.post("/api/analyze", json={"match_id": 100}).json()["prediction"]["predicted_score"]["home"] == 2

    with TestClient(create_app(settings, FakeProvider(context), build(context))) as c:
        job_id = c.post("/api/analyze/start", json={"match_id": 100}).json()["job_id"]
        for _ in range(50):
            job = c.get(f"/api/jobs/{job_id}").json()
            if job["status"] != "running":
                break
            time.sleep(0.05)
        assert job["status"] == "done" and all(s["status"] == "done" for s in job["steps"])
        assert c.get("/api/jobs/nope").status_code == 404


def test_api_maps_agent_errors(settings, context):
    orch = build(context, researcher=[GroqRateLimitError("limit", agent="Researcher")])
    resp = TestClient(create_app(settings, FakeProvider(context), orch)).post("/api/analyze", json={"match_id": 1})
    assert resp.status_code == 429 and resp.json()["error"]["agent"] == "Researcher"


def test_job_reports_error(settings, context):
    orch = build(context, chief=[{"bad": True}, {"bad": True}])
    with TestClient(create_app(settings, FakeProvider(context), orch)) as c:
        job_id = c.post("/api/analyze/start", json={"match_id": 1}).json()["job_id"]
        for _ in range(50):
            job = c.get(f"/api/jobs/{job_id}").json()
            if job["status"] != "running":
                break
            time.sleep(0.05)
        assert job["status"] == "error" and job["error"]["code"] == "invalid_agent_output"
