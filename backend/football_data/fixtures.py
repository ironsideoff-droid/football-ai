from __future__ import annotations

from schemas.match import MatchSummary, TeamRef


def _team(t: dict) -> TeamRef:
    return TeamRef(
        id=t.get("id") or 0,
        name=t.get("name") or t.get("shortName") or "TBD",
        short_name=t.get("shortName"),
        tla=t.get("tla"),
        crest=t.get("crest"),
    )


def normalize_match(m: dict) -> MatchSummary:
    ft = (m.get("score") or {}).get("fullTime") or {}
    comp = m.get("competition") or {}
    return MatchSummary(
        id=m["id"],
        utc_date=m["utcDate"],
        status=m.get("status", "UNKNOWN"),
        competition=comp.get("name"),
        competition_code=comp.get("code"),
        matchday=m.get("matchday"),
        home=_team(m.get("homeTeam") or {}),
        away=_team(m.get("awayTeam") or {}),
        score_home=ft.get("home"),
        score_away=ft.get("away"),
    )


def normalize_fixtures(data: dict) -> list[MatchSummary]:
    matches = [normalize_match(m) for m in data.get("matches", []) if (m.get("homeTeam") or {}).get("id")]
    return sorted(matches, key=lambda m: m.utc_date)
