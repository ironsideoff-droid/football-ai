import asyncio
import json

import httpx
import pytest

from football_data.provider import FootballDataOrgProvider, RATE_LIMIT_PER_MINUTE
from football_data.statistics import extract_standings
from football_data.web_research import WebResearch, WebResearchError, parse_findings
from schemas.match import MatchSummary, TeamRef

MATCH = MatchSummary(id=1, utc_date="2026-11-14T15:00:00Z", status="TIMED", competition="Premier League",
                     home=TeamRef(id=1, name="Home FC"), away=TeamRef(id=2, name="Away United"))


# ------------------------------------------------------------ rate limiting
def test_requests_wait_instead_of_failing_at_the_per_minute_limit():
    now, slept, calls = [0.0], [], []

    async def sleep(s):
        slept.append(s)
        now[0] += s

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(200, json={"matches": []})

    p = FootballDataOrgProvider("t", transport=httpx.MockTransport(handler), sleep=sleep, clock=lambda: now[0], cache_ttl=0)

    async def go():
        for i in range(RATE_LIMIT_PER_MINUTE + 3):
            await p._get(f"/x/{i}")

    asyncio.run(go())
    assert len(calls) == RATE_LIMIT_PER_MINUTE + 3
    assert slept and slept[0] > 59  # the 10th request waited for the window to clear


def test_429_is_retried_up_to_twice_with_the_waiting_time_from_the_api():
    seq = iter([httpx.Response(429, text="Wait 12 seconds"), httpx.Response(429, text="Wait 3 seconds"), httpx.Response(200, json={"ok": 1})])
    waits = []

    async def sleep(s):
        waits.append(s)

    p = FootballDataOrgProvider("t", transport=httpx.MockTransport(lambda r: next(seq)), sleep=sleep, clock=lambda: 0.0)
    assert asyncio.run(p._get("/y")) == {"ok": 1}
    assert waits == [13, 4]


def test_standings_and_team_results_are_cached_for_half_an_hour():
    p = FootballDataOrgProvider("t", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})), cache_ttl=300)
    assert p._ttl_for("/competitions/PL/standings") == 1800 and p._ttl_for("/teams/5/matches") == 1800 and p._ttl_for("/matches/9") == 300


# ------------------------------------------------------------ group-stage tables
def test_group_stage_position_is_read_without_league_averages():
    row = lambda pos, tid, name: {"position": pos, "team": {"id": tid, "name": name}, "playedGames": 2, "won": 1, "draw": 1, "lost": 0, "points": 4,
                                  "goalsFor": 3, "goalsAgainst": 1, "goalDifference": 2}  # noqa: E731
    data = {"competition": {"name": "World Cup"}, "standings": [
        {"stage": "GROUP_STAGE", "type": "TOTAL", "group": "GROUP_A", "table": [row(1, 1, "Home FC"), row(2, 2, "Away United")]},
        {"stage": "GROUP_STAGE", "type": "TOTAL", "group": "GROUP_B", "table": [row(1, 7, "Other")]}]}
    st = extract_standings(data, 1, 2)
    assert st and st.home_team_overall.position == 1 and st.away_team_overall.team == "Away United" and st.table_size == 2
    assert st.league_avg_goals_per_team is None and st.league_avg_home_goals is None
    assert extract_standings(data, 98, 99) is None


# ------------------------------------------------------------ web research
def msg(content, **extra):
    return {"content": content, **extra}


GOOD = {"findings": [
    {"team": "home", "claim": "Striker Smith is doubtful with a hamstring problem.", "url": "https://www.bbc.co.uk/sport/football/1", "source": "BBC Sport", "published": "2026-11-12"},
    {"team": "away", "claim": "Captain Jones is suspended for the trip.", "url": "https://invented.example/fake", "source": "Made Up", "published": None},
    {"team": "away", "claim": "No link here.", "url": "", "source": "x"},
    {"team": "both", "claim": "Wrong team value.", "url": "https://www.bbc.co.uk/sport/football/1"}]}


def test_findings_need_a_url_that_the_search_results_contain():
    evidence = "[1] BBC Sport: Smith doubtful https://www.bbc.co.uk/sport/football/1 hamstring"
    items = parse_findings(msg(evidence + "\n" + json.dumps(GOOD)), MATCH)
    assert [i.title for i in items] == ["Striker Smith is doubtful with a hamstring problem."]  # invented URL, empty URL and bad team dropped
    it = items[0]
    assert it.origin == "web_research" and it.team_name == "Home FC" and it.item_id.startswith("wh-") and it.reliability_class == "high"
    assert it.published_at.startswith("2026-11-12")


def test_answer_without_json_is_an_error():
    with pytest.raises(WebResearchError):
        parse_findings(msg("I could not find anything."), MATCH)


def test_fetch_sends_browser_search_and_reports_provider_refusals():
    seen = {}

    def ok(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": msg("see https://www.bbc.co.uk/sport/football/1\n" + json.dumps(GOOD))}]})

    w = WebResearch("k", "openai/gpt-oss-20b", transport=httpx.MockTransport(ok))
    items = asyncio.run(w.fetch(MATCH))
    assert len(items) == 1 and seen["tools"] == [{"type": "browser_search"}] and seen["model"] == "openai/gpt-oss-20b"
    assert "response_format" not in seen  # browser search cannot be combined with structured outputs

    limit = WebResearch("k", "m", transport=httpx.MockTransport(lambda r: httpx.Response(429, json={"error": {"message": "rate limit"}})))
    with pytest.raises(WebResearchError, match="GROQ_API_KEY_4"):
        asyncio.run(limit.fetch(MATCH))
