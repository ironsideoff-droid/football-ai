from __future__ import annotations

from typing import Optional

from schemas.match import LineupPlayer, Lineups, MatchSummary, TeamLineup, TeamRef


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


def _team_lineup(t: dict) -> Optional[TeamLineup]:
    players = []
    for p in t.get("lineup") or []:
        if isinstance(p, dict) and p.get("name"):
            num = p.get("shirtNumber")
            players.append(LineupPlayer(name=str(p["name"]), position=p.get("position"), shirt_number=num if isinstance(num, int) else None))
    if len(players) != 11:  # a partial or missing list is not a lineup; never guess the rest
        return None
    coach = (t.get("coach") or {}).get("name")
    return TeamLineup(formation=(t.get("formation") or None), coach=coach, starters=players)


def extract_lineups(raw: dict) -> Optional[Lineups]:
    """Real starting elevens, only when the provider supplies both complete lineups for this match."""
    home, away = _team_lineup(raw.get("homeTeam") or {}), _team_lineup(raw.get("awayTeam") or {})
    return Lineups(home=home, away=away) if home and away else None
