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


# ---------------------------------------------------------------- Data Scout (fixtures follow the documented shapes of the
# public ESPN / Understat payloads; they prove the parsers' logic, NOT that the live sites still use these shapes)
import json as _json
from datetime import datetime as _dt, timezone as _tz

from football_data import scout as sc
from schemas.scout import Absence


def test_team_name_matching_handles_common_variants():
    assert sc.same_team("Manchester United FC", "Manchester United")
    assert sc.same_team("Internazionale", "Inter")
    assert sc.same_team("Paris Saint-Germain FC", "Paris SG")
    assert sc.same_team("1. FC Köln", "FC Koln")
    assert not sc.same_team("Manchester United", "Manchester City")
    assert not sc.same_team("Real Madrid", "Real Sociedad")


def _roster(n_starters, formation="4-3-3"):
    ps = [{"starter": True, "jersey": str(i + 1), "athlete": {"displayName": f"P{i}"}, "position": {"abbreviation": "G" if i == 0 else "CD" if i < 5 else "CM" if i < 8 else "F"}}
          for i in range(n_starters)]
    ps.append({"starter": False, "athlete": {"displayName": "Sub"}, "position": {"abbreviation": "F"}})
    return {"formation": formation, "roster": ps}


def test_espn_lineups_need_two_full_elevens():
    ok = sc.parse_lineups({"rosters": [{"homeAway": "home", **_roster(11)}, {"homeAway": "away", **_roster(11, "4-4-2")}]})
    assert ok and ok.home.formation == "4-3-3" and len(ok.away.starters) == 11 and ok.home.starters[0].position == "Goalkeeper"
    assert ok.source == "ESPN"
    assert sc.parse_lineups({"rosters": [{"homeAway": "home", **_roster(11)}, {"homeAway": "away", **_roster(7)}]}) is None
    assert sc.parse_lineups({}) is None


def test_espn_boxscore_stats_and_missing_values():
    summ = {"boxscore": {"teams": [{"team": {"id": "9"}, "statistics": [
        {"name": "totalShots", "displayValue": "14"}, {"name": "shotsOnTarget", "displayValue": "5"}, {"name": "possessionPct", "displayValue": "61.5"}]}]}}
    assert sc.parse_boxscore_stats(summ, "9") == {"shots": 14.0, "sot": 5.0, "possession": 61.5}
    assert sc.parse_boxscore_stats(summ, "10") == {}


def _understat_html(teams: dict) -> str:
    raw = _json.dumps(teams).encode().decode("unicode_escape") if False else _json.dumps(teams)
    esc = "".join(f"\\x{ord(c):02x}" if c in "{}\":,[]" else c for c in raw)  # Understat hex-escapes JSON punctuation
    return f"<script>var teamsData = JSON.parse('{esc}')</script>"


def _hist(day, xg, xga, scored, missed, ha):
    return {"date": f"2026-10-{day:02d} 15:00:00", "xG": str(xg), "xGA": str(xga), "scored": str(scored), "missed": str(missed), "h_a": ha}


def test_understat_xg_and_league_averages():
    teams = {"1": {"id": "1", "title": "Arsenal", "history": [_hist(d, 2.0, 0.5, 2, 0, "h" if d % 2 else "a") for d in range(1, 11)]},
             "2": {"id": "2", "title": "Manchester City", "history": [_hist(d, 1.5, 1.0, 1, 1, "h" if d % 2 else "a") for d in range(1, 11)]}}
    got = sc.parse_understat_teams(_understat_html(teams))
    before = _dt(2026, 11, 1, tzinfo=_tz.utc)
    adv = sc.understat_team_xg(got, "Arsenal FC", before)
    assert adv and adv.matches == 6 and adv.xg_for_avg == 2.0 and adv.xg_against_avg == 0.5 and adv.source == "Understat"
    assert sc.understat_team_xg(got, "Nobody United", before) is None
    assert sc.understat_team_xg(got, "Arsenal", _dt(2026, 9, 1, tzinfo=_tz.utc)) is None  # no match before that date: never fabricate
    assert sc.understat_league_averages(got, before) is None  # fewer than 20 home games: too thin to trust
    with pytest.raises(ValueError):
        sc.parse_understat_teams("<html>no data</html>")


def test_scout_run_reports_each_source_independently_and_applies_only_what_it_found():
    html = _understat_html({"1": {"id": "1", "title": "Arsenal", "history": [_hist(d, 2.0, 0.5, 2, 0, "h") for d in range(1, 8)]},
                            "2": {"id": "2", "title": "Chelsea", "history": [_hist(d, 1.0, 1.0, 1, 1, "a") for d in range(1, 8)]}})

    def handler(req: httpx.Request) -> httpx.Response:
        if "understat.com" in str(req.url):
            return httpx.Response(200, text=html)
        return httpx.Response(503)  # ESPN down

    match = _make_match("PL", "Arsenal FC", "Chelsea FC")
    scout = sc.Scout(transport=httpx.MockTransport(handler))
    rep = asyncio.run(scout.run(match))
    assert "failed" in rep.notes["espn"] and rep.notes["understat"].startswith("xG found")
    assert rep.home_adv and rep.home_adv.xg_for_avg == 2.0 and rep.lineups is None

    ctx = _make_ctx(match)
    sc.apply_scout(ctx, rep, [Absence(team="home", player="X", status="injured", source="s", url="https://s.test/a")], "n/a")
    assert ctx.home_advanced.xg_for_avg == 2.0 and ctx.lineups is None and len(ctx.absences) == 1
    assert ctx.data_availability["expected_goals_xg"].startswith("available")
    assert ctx.data_availability["lineups_formations"].startswith("unavailable")
    assert any(s.id == "understat" for s in ctx.sources)


def _make_match(code, home, away):
    from schemas.match import MatchSummary, TeamRef
    return MatchSummary(id=1, utc_date="2026-11-01T15:00:00Z", status="TIMED", competition_code=code, competition="X",
                        home=TeamRef(id=1, name=home), away=TeamRef(id=2, name=away))


def _make_ctx(match):
    from schemas.match import MatchContext
    return MatchContext(match=match, retrieved_at="2026-11-01T00:00:00Z", data_availability={})


def test_unmapped_competition_is_noted_not_guessed():
    rep = asyncio.run(sc.Scout(transport=httpx.MockTransport(lambda r: httpx.Response(500))).run(_make_match("XYZ", "A", "B")))
    assert "no ESPN league mapping" in rep.notes["espn"] and rep.home_adv is None


def test_web_absences_need_a_url_found_in_the_evidence():
    from football_data.web_research import parse_absences
    evidence = "search result page https://good.test/team-news about the squad"
    data = {"absences": [
        {"team": "home", "player": "A Player", "status": "injured", "detail": "hamstring", "url": "https://good.test/team-news", "source": "Good"},
        {"team": "home", "player": "B Player", "status": "suspended", "url": "https://invented.test/x", "source": "Bad"},
        {"team": "away", "player": "C Player", "status": "injured", "url": "", "source": "NoUrl"},
        {"team": "away", "player": "A Player", "status": "weird", "url": "https://good.test/team-news"}]}
    out = parse_absences(data, evidence)
    assert [(a.team, a.player, a.status) for a in out] == [("home", "A Player", "injured"), ("away", "A Player", "unknown")]


def test_quality_counts_scraped_data_and_names_what_is_still_missing():
    from football_data.source_manager import assess_quality
    from schemas.scout import TeamAdvanced
    ctx = _make_ctx(_make_match("PL", "A", "B"))
    base = assess_quality(ctx)
    ctx.home_advanced = TeamAdvanced(team="A", source="Understat", matches=5, xg_for_avg=1.8, xg_against_avg=0.9)
    ctx.absences = [Absence(team="home", player="X", source="s")]
    better = assess_quality(ctx)
    assert better.score > base.score and better.components["xg"] and better.components["injuries_suspensions"]
    assert not better.components["lineups"] and any("Confirmed lineups: not found" in i.detail for i in better.issues)


# ---------------------------------------------------------------- deep search (4 role-based engines)
def _rss(n, tag, team="Arsenal"):
    items = "".join(f"<item><title>{team} {tag} story {i} - Src{i % 5}</title><link>https://n.test/{tag}/{i}</link><source url='https://src{i % 5}.test'>Src{i % 5}</source>"
                    f"<pubDate>Thu, 12 Nov 2026 10:00:00 GMT</pubDate></item>" for i in range(n))
    return f"<rss><channel>{items}</channel></rss>"


def test_deep_search_collects_per_engine_dedupes_and_caps_at_300():
    from football_data.deep_search import DeepSearch, MAX_TOTAL, PER_ENGINE_CAP, digest

    def handler(req: httpx.Request) -> httpx.Response:
        q = req.url.params["q"]
        team = "Chelsea" if '"Chelsea FC" football' in q and '"Arsenal FC"' not in q.split("football")[0] else "Arsenal"
        return httpx.Response(200, text=_rss(40, abs(hash(q)) % 10**8, team))  # every query returns 40 distinct headlines

    m = _make_match("PL", "Arsenal FC", "Chelsea FC")
    summary, kept = asyncio.run(DeepSearch(transport=httpx.MockTransport(handler), clock=lambda: 0).run(m))
    assert summary.total == MAX_TOTAL == 300 and not summary.below_target
    assert {e.key for e in summary.engines} == {"statistical", "intelligence", "tactical", "context"}
    assert all(e.found == PER_ENGINE_CAP for e in summary.engines) and summary.queries > 60
    assert len({i.title for i in summary.items}) == 300  # no duplicates across engines
    d = digest(summary, kept["tactical"])
    assert d["headlines_found"] == 75 and len(d["items"]) == 24 and d["items"][0]["id"].startswith("dt-")


def test_deep_search_reports_a_short_result_honestly_and_survives_failures():
    from football_data.deep_search import DeepSearch
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] % 3 == 0:
            return httpx.Response(503)
        return httpx.Response(200, text=_rss(2, calls["n"]))

    summary, _ = asyncio.run(DeepSearch(transport=httpx.MockTransport(handler), clock=lambda: 0).run(_make_match("PL", "Arsenal FC", "Chelsea FC")))
    assert summary.below_target and summary.failed_queries > 0 and 0 < summary.total < 100
    assert any("nothing was padded" in n for n in summary.notes)


def test_deep_search_drops_headlines_that_do_not_mention_the_team_and_classifies_topics():
    from football_data.deep_search import DeepSearch, classify_topic
    xml = ("<rss><channel><item><title>Totally unrelated cricket news - X</title><link>https://n.test/1</link><source url='https://x.test'>X</source>"
           "<pubDate>Thu, 12 Nov 2026 10:00:00 GMT</pubDate></item><item><title>Arsenal striker ruled out with hamstring injury - BBC</title>"
           "<link>https://n.test/2</link><source url='https://bbc.co.uk'>BBC</source><pubDate>Thu, 12 Nov 2026 10:00:00 GMT</pubDate></item></channel></rss>")
    summary, _ = asyncio.run(DeepSearch(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=xml)), clock=lambda: 0)
                             .run(_make_match("PL", "Arsenal FC", "Chelsea FC")))
    assert [i.title for i in summary.items] == ["Arsenal striker ruled out with hamstring injury"] and summary.items[0].topic == "injuries"
    assert classify_topic("Predicted lineup and formation for Sunday") == "lineups" and classify_topic("Weather forecast") == "referee_conditions"
