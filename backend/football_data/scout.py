"""Data Scout (the 4th agent's scraping half): fills the gaps football-data.org leaves, from public pages and JSON endpoints.

Sources (each one fails independently; a failure only becomes a note, never an invented number):
* ESPN public site API  -> confirmed starting elevens + formations (usually published ~1h before kickoff), and recent-match
  shots / shots on target / possession averages from the teams' last finished matches.
* Understat (embedded JSON on the league page) -> expected goals (xG / xGA) for the big-5 leagues, plus league-wide home/away
  goal averages computed from the same table.
Injuries / suspensions come from the web-research step (see web_research.py), which is merged in by the orchestrator.

These are unofficial endpoints whose shape can change without notice, so every parser is defensive and everything it cannot
read is simply left out. Nothing here is estimated.
"""
from __future__ import annotations

import asyncio
import codecs
import json
import logging
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, Optional

import httpx

from schemas.match import H2HSummary, LineupPlayer, Lineups, MatchContext, MatchSummary, TeamLineup
from schemas.scout import Absence, LeagueGoalAverages, TeamAdvanced
from schemas.sources import SourceRecord

log = logging.getLogger("scout")

ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer"
UNDERSTAT = "https://understat.com/league"
ESPN_SLUGS = {
    "WC": "fifa.world", "EC": "uefa.euro", "PL": "eng.1", "ELC": "eng.2", "PD": "esp.1", "BL1": "ger.1", "SA": "ita.1",
    "FL1": "fra.1", "DED": "ned.1", "PPL": "por.1", "CL": "uefa.champions", "BSA": "bra.1", "CLI": "conmebol.libertadores",
}
UNDERSTAT_LEAGUES = {"PL": "EPL", "PD": "La_liga", "BL1": "Bundesliga", "SA": "Serie_A", "FL1": "Ligue_1"}
RECENT_N = 5
CACHE_SECONDS = 1800
UA = {"User-Agent": "Mozilla/5.0 (compatible; TACTIQ-scout/1.0)", "Accept": "application/json,text/html;q=0.9,*/*;q=0.5"}

# ---------------------------------------------------------------- team-name matching
_NOISE = {"fc", "cf", "afc", "ac", "as", "sc", "ssc", "us", "cd", "ud", "rc", "rcd", "sv", "vfl", "vfb", "tsg", "1", "fk", "the"}
_ALIASES = {"internazionale": "inter", "inter milan": "inter", "manchester utd": "manchester united", "man united": "manchester united",
            "man city": "manchester city", "spurs": "tottenham", "tottenham hotspur": "tottenham", "wolverhampton wanderers": "wolves",
            "wolverhampton": "wolves", "newcastle united": "newcastle", "west ham united": "west ham", "brighton and hove albion": "brighton",
            "paris saint germain": "psg", "paris sg": "psg", "atletico madrid": "atletico", "club atletico de madrid": "atletico",
            "borussia monchengladbach": "gladbach", "bayer leverkusen": "leverkusen", "bayer 04 leverkusen": "leverkusen",
            "eintracht frankfurt": "frankfurt", "rb leipzig": "leipzig", "usa": "united states", "korea republic": "south korea",
            "ir iran": "iran", "cote d ivoire": "ivory coast"}


def norm_team(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = " ".join(t for t in s.split() if t not in _NOISE)
    return _ALIASES.get(s, s)


def same_team(a: str, b: str) -> bool:
    x, y = norm_team(a), norm_team(b)
    if not x or not y:
        return False
    if x == y:
        return True
    short, long_ = sorted((x, y), key=len)
    if len(short) >= 4 and (short in long_.split() or long_.startswith(short + " ") or long_.endswith(" " + short)):
        return True
    return SequenceMatcher(None, x, y).ratio() >= 0.86


# ---------------------------------------------------------------- tiny helpers
def _num(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(str(v).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _avg(values: list[float]) -> Optional[float]:
    return round(sum(values) / len(values), 2) if values else None


def _comp(ev: dict) -> dict:
    return (ev.get("competitions") or [{}])[0] or {}


def _team_of(c: dict) -> dict:
    return c.get("team") or {}


def _completed(ev: dict) -> bool:
    for st in (ev.get("status"), _comp(ev).get("status")):
        t = (st or {}).get("type") or {}
        if t.get("completed") is True or t.get("state") == "post":
            return True
    return False


def _pos_group(abbr: str) -> str:
    """Map ESPN position labels to football-data.org-style groups used by the pitch drawing."""
    a = (abbr or "").upper()
    if a in ("G", "GK"):
        return "Goalkeeper"
    if a in ("D", "CD", "CB", "LB", "RB", "SW", "LWB", "RWB") or a.startswith(("CD", "CB")):
        return "Defence"
    if a in ("M", "CM", "DM", "AM", "LM", "RM", "CDM", "CAM", "MF") or a.endswith("M") and len(a) <= 3:
        return "Midfield"
    return "Offence"


# ---------------------------------------------------------------- ESPN
def parse_lineups(summary: dict, source: str = "ESPN") -> Optional[Lineups]:
    sides: dict[str, TeamLineup] = {}
    for r in summary.get("rosters") or []:
        side = r.get("homeAway")
        starters = []
        for p in r.get("roster") or []:
            if not p.get("starter"):
                continue
            ath, pos = p.get("athlete") or {}, p.get("position") or {}
            name = ath.get("displayName") or ath.get("fullName")
            if not name:
                continue
            jersey = _num(p.get("jersey"))
            starters.append(LineupPlayer(name=str(name), position=_pos_group(pos.get("abbreviation") or pos.get("name") or ""),
                                         shirt_number=int(jersey) if jersey is not None else None))
        if side in ("home", "away") and len(starters) == 11:  # a partial list is not a lineup; never guess the rest
            sides[side] = TeamLineup(formation=(r.get("formation") or None), starters=starters)
    return Lineups(home=sides["home"], away=sides["away"], source=source) if len(sides) == 2 else None


def parse_boxscore_stats(summary: dict, team_id: str) -> dict[str, Optional[float]]:
    for t in (summary.get("boxscore") or {}).get("teams") or []:
        if str(_team_of(t).get("id")) != str(team_id):
            continue
        by = {s.get("name"): _num(s.get("displayValue", s.get("value"))) for s in t.get("statistics") or []}
        return {"shots": by.get("totalShots"), "sot": by.get("shotsOnTarget"), "possession": by.get("possessionPct")}
    return {}


def parse_h2h(summary: dict, home_name: str, away_name: str) -> Optional[H2HSummary]:
    """ESPN's `headToHeadGames` (when present): earlier meetings with scores. Anything not clearly parseable is skipped."""
    hw = aw = dr = hg = ag = 0
    recent: list[dict] = []
    seen: set[str] = set()
    for block in summary.get("headToHeadGames") or []:
        for ev in block.get("events") or []:
            key = str(ev.get("id") or ev.get("gameDate") or len(seen))
            if key in seen:
                continue
            seen.add(key)
            scores: dict[str, float] = {}
            for c in ev.get("competitors") or _comp(ev).get("competitors") or []:
                nm = _team_of(c).get("displayName") or c.get("displayName") or ""
                sc = _num((c.get("score") or {}).get("value") if isinstance(c.get("score"), dict) else c.get("score"))
                if sc is not None and nm:
                    scores[nm] = sc
            h = next((v for k, v in scores.items() if same_team(k, home_name)), None)
            a = next((v for k, v in scores.items() if same_team(k, away_name)), None)
            if h is None or a is None:
                continue
            hg, ag = hg + int(h), ag + int(a)
            hw, aw, dr = hw + (h > a), aw + (a > h), dr + (h == a)
            recent.append({"date": str(ev.get("gameDate") or ev.get("date") or "")[:10], "home_goals": int(h), "away_goals": int(a)})
    n = hw + aw + dr
    return H2HSummary(matches_considered=n, upcoming_home_team_wins=hw, draws=dr, upcoming_away_team_wins=aw,
                      upcoming_home_team_goals=hg, upcoming_away_team_goals=ag, recent=recent[:10]) if n else None


class EspnScraper:
    def __init__(self, http: httpx.AsyncClient):
        self.http = http

    async def _json(self, url: str, params: Optional[dict] = None) -> dict:
        r = await self.http.get(url, params=params)
        r.raise_for_status()
        return r.json()

    async def find_event(self, slug: str, match: MatchSummary) -> Optional[tuple[str, dict]]:
        ko = datetime.fromisoformat(match.utc_date.replace("Z", "+00:00"))
        rng = f"{(ko - timedelta(days=1)):%Y%m%d}-{(ko + timedelta(days=1)):%Y%m%d}"
        data = await self._json(f"{ESPN}/{slug}/scoreboard", {"dates": rng, "limit": 200})
        for ev in data.get("events") or []:
            cs = _comp(ev).get("competitors") or []
            names = {c.get("homeAway"): (_team_of(c).get("displayName") or "") for c in cs}
            if same_team(names.get("home", ""), match.home.name) and same_team(names.get("away", ""), match.away.name):
                return str(ev.get("id")), {c.get("homeAway"): str(_team_of(c).get("id")) for c in cs}
        return None

    async def recent_stats(self, slug: str, team_id: str, before: datetime, team_name: str) -> Optional[TeamAdvanced]:
        sched = await self._json(f"{ESPN}/{slug}/teams/{team_id}/schedule")
        done = []
        for ev in sched.get("events") or []:
            try:
                d = datetime.fromisoformat(str(ev.get("date")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if _completed(ev) and d < before:
                done.append((d, str(ev.get("id"))))
        done = [e for _, e in sorted(done, reverse=True)[:RECENT_N]]

        async def one(eid: str) -> dict:
            try:
                return parse_boxscore_stats(await self._json(f"{ESPN}/{slug}/summary", {"event": eid}), team_id)
            except (httpx.HTTPError, ValueError):
                return {}

        rows = [r for r in await asyncio.gather(*(one(e) for e in done)) if r]
        col = lambda k: [r[k] for r in rows if r.get(k) is not None]  # noqa: E731
        if not any(col(k) for k in ("shots", "sot", "possession")):
            return None
        return TeamAdvanced(team=team_name, source="ESPN", url=f"https://www.espn.com/soccer/team/_/id/{team_id}", matches=len(rows),
                            shots_avg=_avg(col("shots")), shots_on_target_avg=_avg(col("sot")), possession_avg=_avg(col("possession")))


# ---------------------------------------------------------------- Understat
def parse_understat_teams(html: str) -> dict:
    m = re.search(r"teamsData\s*=\s*JSON\.parse\('(.*?)'\)", html, re.S)
    if not m:
        raise ValueError("teamsData not found on the Understat page")
    return json.loads(codecs.decode(m.group(1), "unicode_escape"))


def understat_team_xg(teams: dict, team_name: str, before: datetime, n: int = 6) -> Optional[TeamAdvanced]:
    for t in teams.values():
        if not same_team(str(t.get("title", "")), team_name):
            continue
        hist = []
        for h in t.get("history") or []:
            try:
                d = datetime.strptime(str(h["date"])[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except (KeyError, ValueError):
                continue
            if d < before and _num(h.get("xG")) is not None and _num(h.get("xGA")) is not None:
                hist.append((d, float(h["xG"]), float(h["xGA"])))
        hist = sorted(hist)[-n:]
        if not hist:
            return None
        return TeamAdvanced(team=team_name, source="Understat", url="https://understat.com", matches=len(hist),
                            xg_for_avg=_avg([x for _, x, _ in hist]), xg_against_avg=_avg([y for _, _, y in hist]))
    return None


def understat_league_averages(teams: dict, before: datetime) -> Optional[LeagueGoalAverages]:
    hg = ag = games = 0
    for t in teams.values():
        for h in t.get("history") or []:
            try:
                d = datetime.strptime(str(h["date"])[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except (KeyError, ValueError):
                continue
            if d < before and h.get("h_a") == "h":
                hg, ag, games = hg + int(h["scored"]), ag + int(h["missed"]), games + 1
    return LeagueGoalAverages(source="Understat", home_goals_avg=round(hg / games, 3), away_goals_avg=round(ag / games, 3), games=games) \
        if games >= 20 else None


def understat_season(ko: datetime) -> int:
    return ko.year if ko.month >= 7 else ko.year - 1


# ---------------------------------------------------------------- orchestration
class ScoutReport:
    def __init__(self) -> None:
        self.lineups: Optional[Lineups] = None
        self.h2h: Optional[H2HSummary] = None
        self.home_adv: Optional[TeamAdvanced] = None
        self.away_adv: Optional[TeamAdvanced] = None
        self.league: Optional[LeagueGoalAverages] = None
        self.notes: dict[str, str] = {}  # per source: what worked / why it did not
        self.sources: list[SourceRecord] = []


class Scout:
    def __init__(self, transport: Optional[httpx.AsyncBaseTransport] = None, timeout: float = 20.0, clock=time.monotonic):
        self.http = httpx.AsyncClient(timeout=timeout, headers=UA, follow_redirects=True, transport=transport)
        self.espn = EspnScraper(self.http)
        self._clock = clock
        self._cache: dict[str, tuple[float, Any]] = {}

    async def aclose(self) -> None:
        await self.http.aclose()

    async def _cached(self, key: str, make):
        hit = self._cache.get(key)
        if hit and self._clock() - hit[0] < CACHE_SECONDS:
            return hit[1]
        value = await make()
        self._cache[key] = (self._clock(), value)
        return value

    async def _understat_teams(self, league: str, season: int) -> dict:
        async def make():
            r = await self.http.get(f"{UNDERSTAT}/{league}/{season}")
            r.raise_for_status()
            return parse_understat_teams(r.text)
        return await self._cached(f"us:{league}:{season}", make)

    async def run(self, match: MatchSummary) -> ScoutReport:
        rep = ScoutReport()
        code = match.competition_code or ""
        ko = datetime.fromisoformat(match.utc_date.replace("Z", "+00:00"))

        async def espn() -> None:
            slug = ESPN_SLUGS.get(code)
            if not slug:
                rep.notes["espn"] = f"no ESPN league mapping for competition '{code or match.competition}'"
                return
            found = await self.espn.find_event(slug, match)
            if not found:
                rep.notes["espn"] = "match not found on the ESPN scoreboard (name or date mismatch)"
                return
            eid, ids = found
            summary = await self.espn._json(f"{ESPN}/{slug}/summary", {"event": eid})
            parts = []
            rep.lineups = parse_lineups(summary)
            parts.append("lineups " + ("found" if rep.lineups else "not published yet"))
            rep.h2h = parse_h2h(summary, match.home.name, match.away.name)
            parts.append("head-to-head " + ("found" if rep.h2h else "not provided"))
            advs = await asyncio.gather(
                self.espn.recent_stats(slug, ids.get("home", ""), ko, match.home.name),
                self.espn.recent_stats(slug, ids.get("away", ""), ko, match.away.name), return_exceptions=True)
            rep.home_adv = advs[0] if not isinstance(advs[0], BaseException) else None
            rep.away_adv = advs[1] if not isinstance(advs[1], BaseException) else None
            parts.append("shots/possession " + ("found" if (rep.home_adv or rep.away_adv) else "not provided"))
            rep.notes["espn"] = "; ".join(parts)
            rep.sources.append(SourceRecord(id="espn", name="ESPN public site API", url=f"{ESPN}/{slug}/summary?event={eid}",
                                            retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), reliability="high",
                                            freshness="live", note="lineups, head-to-head, recent shots and possession"))

        async def understat() -> None:
            league = UNDERSTAT_LEAGUES.get(code)
            if not league:
                rep.notes["understat"] = "xG source covers only the big-5 European leagues"
                return
            teams = await self._understat_teams(league, understat_season(ko))
            hx, ax = understat_team_xg(teams, match.home.name, ko), understat_team_xg(teams, match.away.name, ko)
            rep.league = understat_league_averages(teams, ko)
            rep.notes["understat"] = f"xG {'found' if (hx or ax) else 'team not found'}; league averages {'found' if rep.league else 'not enough games'}"
            if hx or ax:
                rep.sources.append(SourceRecord(id="understat", name="Understat", url=f"{UNDERSTAT}/{league}/{understat_season(ko)}",
                                                retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), reliability="medium",
                                                freshness="live", note="expected goals (xG/xGA), last 6 matches"))
            # xG may come from here while shots/possession come from ESPN: keep both in one record per team
            for side, adv in (("home", hx), ("away", ax)):
                if not adv:
                    continue
                cur = getattr(rep, f"{side}_adv")
                if cur:
                    cur.xg_for_avg, cur.xg_against_avg, cur.source = adv.xg_for_avg, adv.xg_against_avg, f"{cur.source} + Understat"
                else:
                    setattr(rep, f"{side}_adv", adv)

        async def guarded(name: str, fn) -> None:
            try:
                await asyncio.wait_for(fn(), timeout=35)
            except Exception as exc:  # noqa: BLE001  every source is optional
                rep.notes[name] = f"failed: {type(exc).__name__}: {str(exc)[:120]}"
                log.warning("scout source %s failed: %s", name, exc)

        # understat merges into espn's records, so run espn first, then understat
        await guarded("espn", espn)
        await guarded("understat", understat)
        return rep


def apply_scout(ctx: MatchContext, rep: ScoutReport, absences: list[Absence], web_note: str) -> None:
    """Merge scraped data into the context. Existing football-data.org values always win."""
    av = ctx.data_availability
    if ctx.lineups is None and rep.lineups:
        ctx.lineups = rep.lineups
        av["lineups_formations"] = "available: confirmed starting elevens from ESPN"
    elif ctx.lineups is None:
        av["lineups_formations"] = f"unavailable: {rep.notes.get('espn', 'ESPN not queried')}"
    if (ctx.head_to_head is None or ctx.head_to_head.matches_considered == 0) and rep.h2h:
        ctx.head_to_head = rep.h2h
        av["head_to_head"] = "available: from ESPN"
    ctx.home_advanced, ctx.away_advanced = rep.home_adv, rep.away_adv
    xg = any(a and a.xg_for_avg is not None for a in (rep.home_adv, rep.away_adv))
    adv = any(a and (a.shots_avg is not None or a.possession_avg is not None) for a in (rep.home_adv, rep.away_adv))
    av["expected_goals_xg"] = "available: " + rep.notes.get("understat", "") if xg else f"unavailable: {rep.notes.get('understat', 'not queried')}"
    av["advanced_stats"] = "available: " + rep.notes.get("espn", "") if adv else f"unavailable: {rep.notes.get('espn', 'not queried')}"
    ctx.absences = absences
    av["injuries_suspensions"] = (f"available: {len(absences)} reported absence(s) from web research (unverified)" if absences
                                  else f"unavailable: {web_note}")
    st = ctx.standings
    if st and rep.league and not st.league_avg_home_goals:
        st.league_avg_home_goals, st.league_avg_away_goals = rep.league.home_goals_avg, rep.league.away_goals_avg
        st.league_avg_goals_per_team = round((rep.league.home_goals_avg + rep.league.away_goals_avg) / 2, 3)
        av["league_goal_averages"] = f"available: computed from {rep.league.games} {rep.league.source} matches"
    ctx.sources = [*ctx.sources, *rep.sources]
