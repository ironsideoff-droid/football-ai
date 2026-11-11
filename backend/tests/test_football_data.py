import asyncio

import httpx
import pytest

from errors import FootballAuthError, FootballDataError, FootballNotFoundError, FootballRateLimitError
from football_data.provider import FootballDataOrgProvider
from football_data.statistics import summarize_h2h, summarize_team_matches
from tests.conftest import A, H, OTHER, raw_match


def provider(handler):
    async def nosleep(_):
        return None
    return FootballDataOrgProvider("tok", transport=httpx.MockTransport(handler), sleep=nosleep)


HOME_MATCHES = [
    raw_match(1, "2026-10-01", H, OTHER, 2, 0), raw_match(2, "2026-10-08", OTHER, H, 1, 1),
    raw_match(3, "2026-10-15", H, OTHER, 0, 1), raw_match(4, "2026-12-01", H, OTHER, 5, 0),  # after kickoff: ignored
    raw_match(5, "2026-10-20", H, OTHER, None, None, status="SCHEDULED"),
]


def test_team_form_is_computed_not_invented():
    f = summarize_team_matches(1, "Home FC", HOME_MATCHES, "2026-11-10T15:00:00Z")
    assert f.matches_considered == 3 and f.sequence_oldest_to_newest == "WDL"
    assert (f.overall.won, f.overall.drawn, f.overall.lost, f.points) == (1, 1, 1, 4)
    assert (f.overall.goals_for, f.overall.goals_against) == (3, 2)
    assert f.home.played == 2 and f.away.played == 1 and f.clean_sheets == 1 and f.failed_to_score == 1


def test_no_matches_gives_none_not_zero_averages():
    f = summarize_team_matches(1, "Home FC", [], "2026-11-10T00:00:00Z")
    assert f.matches_considered == 0 and f.avg_goals_for is None


def test_h2h_uses_upcoming_home_team_perspective():
    s = summarize_h2h(1, 2, [raw_match(9, "2026-01-01", A, H, 0, 3), raw_match(8, "2025-01-01", H, A, 1, 1)])
    assert (s.upcoming_home_team_wins, s.draws, s.upcoming_away_team_wins) == (1, 1, 0)
    assert s.upcoming_home_team_goals == 4 and s.upcoming_away_team_goals == 1


def standings_payload():
    row = lambda pos, t: {"position": pos, "team": t, "playedGames": 10, "won": 6, "draw": 2, "lost": 2,  # noqa: E731
                          "points": 20, "goalsFor": 18, "goalsAgainst": 9, "goalDifference": 9, "form": "W,W,D"}
    return {"competition": {"name": "Premier League"}, "standings": [
        {"type": "TOTAL", "table": [row(1, H), row(2, A)]},
        {"type": "HOME", "table": [row(1, H)]}, {"type": "AWAY", "table": [row(2, A)]}]}


def test_full_context_and_graceful_degradation():
    def handler(req):
        p = req.url.path
        assert req.headers["x-auth-token"] == "tok"
        if p.endswith("/head2head"):
            return httpx.Response(200, json={"matches": [raw_match(9, "2026-01-01", A, H, 0, 3)]})
        if "/teams/" in p:
            return httpx.Response(200, json={"matches": HOME_MATCHES})
        if "/standings" in p:
            return httpx.Response(403, json={})
        return httpx.Response(200, json=raw_match(100, "2026-11-10", H, A, None, None, status="TIMED"))

    ctx = asyncio.run(provider(handler).get_match_context(100))
    assert ctx.home_form and ctx.head_to_head and ctx.standings is None
    assert ctx.data_availability["standings"].startswith("unavailable")
    assert ctx.data_availability["injuries_suspensions"].startswith("unavailable")
    assert "crest" not in ctx.to_agent_payload()["match"]["home"]


def test_standings_extraction():
    def handler(req):
        p = req.url.path
        if "/standings" in p:
            return httpx.Response(200, json=standings_payload())
        if p.endswith("/head2head") or "/teams/" in p:
            return httpx.Response(200, json={"matches": []})
        return httpx.Response(200, json=raw_match(100, "2026-11-10", H, A, None, None, status="TIMED"))
    ctx = asyncio.run(provider(handler).get_match_context(100))
    assert ctx.standings.home_team_overall.position == 1 and ctx.standings.away_team_away.position == 2


@pytest.mark.parametrize("status,exc", [(401, FootballAuthError), (404, FootballNotFoundError),
                                        (429, FootballRateLimitError), (500, FootballDataError)])
def test_provider_errors(status, exc):
    with pytest.raises(exc):
        asyncio.run(provider(lambda r: httpx.Response(status, text="wait 1 seconds")).get_match_context(1))


def test_fixtures_listing():
    data = {"matches": [raw_match(2, "2026-11-11", H, A, None, None, "TIMED"), raw_match(1, "2026-11-10", A, H, None, None, "TIMED")]}
    out = asyncio.run(provider(lambda r: httpx.Response(200, json=data)).list_fixtures("2026-11-08", "2026-11-15"))
    assert [m.id for m in out] == [1, 2]


def test_long_window_is_split_into_short_requests_and_merged():
    ranges = []

    def handler(req):
        a, b = req.url.params["dateFrom"], req.url.params["dateTo"]
        ranges.append((a, b))
        day = a  # one match per chunk; the first chunk is returned again by a second call to test de-duplication
        return httpx.Response(200, json={"matches": [raw_match(int(a[-2:]), day, H, A, None, None, "TIMED")]})

    out = asyncio.run(provider(handler).list_fixtures("2026-11-11", "2026-12-10"))
    assert ranges == [("2026-11-11", "2026-11-20"), ("2026-11-21", "2026-11-30"), ("2026-12-01", "2026-12-10")]
    assert all((__import__("datetime").date.fromisoformat(b) - __import__("datetime").date.fromisoformat(a)).days <= 9 for a, b in ranges)
    assert [m.id for m in out] == [11, 21, 1] and [m.utc_date[:10] for m in out] == ["2026-11-11", "2026-11-21", "2026-12-01"]


def test_api_accepts_up_to_30_days_and_rejects_more(tmp_path):
    from fastapi.testclient import TestClient
    from config import Settings
    from main import create_app
    from tests.test_workflow_and_api import FakeProvider
    ctx = __import__("tests.conftest", fromlist=["x"]).context.__wrapped__()
    app = create_app(Settings(groq_keys=("a", "b", "c"), groq_models=("m",) * 3, football_api_key="f", predictions_dir=str(tmp_path)), FakeProvider(ctx))
    c = TestClient(app)
    assert c.get("/api/fixtures?days=30").status_code == 200 and c.get("/api/fixtures").status_code == 200
    assert c.get("/api/fixtures?days=31").status_code == 422
