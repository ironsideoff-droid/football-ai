"""Deterministic statistics computed from raw provider data (no LLM involved)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from schemas.match import (
    FormSplit,
    H2HSummary,
    RecentMatch,
    StandingRow,
    StandingsContext,
    TeamForm,
)


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _score(match: dict) -> Optional[tuple[int, int]]:
    ft = (match.get("score") or {}).get("fullTime") or {}
    h, a = ft.get("home"), ft.get("away")
    return (int(h), int(a)) if h is not None and a is not None else None


def _add(split: FormSplit, gf: int, ga: int) -> None:
    split.played += 1
    split.goals_for += gf
    split.goals_against += ga
    split.won += int(gf > ga)
    split.drawn += int(gf == ga)
    split.lost += int(gf < ga)


def summarize_team_matches(
    team_id: int, team_name: str, matches: list[dict], before_iso: str, n: int = 10
) -> TeamForm:
    finished = [
        m for m in matches
        if m.get("status") == "FINISHED" and _score(m) and (m.get("utcDate") or "") < before_iso
    ]
    finished.sort(key=lambda m: m["utcDate"])
    last = finished[-n:]

    overall, home, away = FormSplit(), FormSplit(), FormSplit()
    recent: list[RecentMatch] = []
    clean = failed = 0
    for m in last:
        hs, as_ = _score(m)  # type: ignore[misc]
        is_home = (m.get("homeTeam") or {}).get("id") == team_id
        gf, ga = (hs, as_) if is_home else (as_, hs)
        _add(overall, gf, ga)
        _add(home if is_home else away, gf, ga)
        clean += int(ga == 0)
        failed += int(gf == 0)
        opp = (m.get("awayTeam") if is_home else m.get("homeTeam")) or {}
        recent.append(
            RecentMatch(
                date=m["utcDate"][:10],
                competition=(m.get("competition") or {}).get("name"),
                opponent=opp.get("name", "?"),
                venue="H" if is_home else "A",
                goals_for=gf,
                goals_against=ga,
                result="W" if gf > ga else "D" if gf == ga else "L",
            )
        )
    played = overall.played
    points = overall.won * 3 + overall.drawn
    last_date = last[-1]["utcDate"] if last else None
    rest_days = congestion = None
    if last_date:
        kickoff = _parse(before_iso)
        rest_days = round((kickoff - _parse(last_date)).total_seconds() / 86400, 1)
        congestion = sum(1 for m in finished if 0 < (kickoff - _parse(m["utcDate"])).days <= 30)
    return TeamForm(
        team=team_name,
        matches_considered=played,
        overall=overall,
        home=home,
        away=away,
        points=points,
        points_per_game=round(points / played, 2) if played else None,
        avg_goals_for=round(overall.goals_for / played, 2) if played else None,
        avg_goals_against=round(overall.goals_against / played, 2) if played else None,
        clean_sheets=clean,
        failed_to_score=failed,
        sequence_oldest_to_newest="".join(r.result for r in recent),
        recent_matches=recent,
        last_match_date=last_date,
        rest_days=rest_days,
        matches_last_30_days=congestion or 0,
    )


def summarize_h2h(home_id: int, away_id: int, matches: list[dict], n: int = 10) -> H2HSummary:
    finished = sorted(
        [m for m in matches if m.get("status") == "FINISHED" and _score(m)],
        key=lambda m: m["utcDate"],
    )[-n:]
    hw = dr = aw = hg = ag = 0
    recent: list[dict[str, Any]] = []
    for m in finished:
        hs, as_ = _score(m)  # type: ignore[misc]
        home_is_upcoming_home = (m.get("homeTeam") or {}).get("id") == home_id
        gf, ga = (hs, as_) if home_is_upcoming_home else (as_, hs)
        hg, ag = hg + gf, ag + ga
        hw, dr, aw = hw + int(gf > ga), dr + int(gf == ga), aw + int(gf < ga)
        recent.append({
            "date": m["utcDate"][:10],
            "home": (m.get("homeTeam") or {}).get("name"),
            "away": (m.get("awayTeam") or {}).get("name"),
            "score": f"{hs}-{as_}",
        })
    return H2HSummary(
        matches_considered=len(finished),
        upcoming_home_team_wins=hw,
        draws=dr,
        upcoming_away_team_wins=aw,
        upcoming_home_team_goals=hg,
        upcoming_away_team_goals=ag,
        recent=recent,
    )


def _row(table: list[dict], team_id: int) -> Optional[StandingRow]:
    for r in table:
        if (r.get("team") or {}).get("id") == team_id:
            return StandingRow(
                position=r.get("position", 0),
                team=r["team"].get("name", "?"),
                played=r.get("playedGames", 0),
                won=r.get("won", 0),
                draw=r.get("draw", 0),
                lost=r.get("lost", 0),
                points=r.get("points", 0),
                goals_for=r.get("goalsFor", 0),
                goals_against=r.get("goalsAgainst", 0),
                goal_difference=r.get("goalDifference", 0),
                form=r.get("form"),
            )
    return None


def _group_standings(data: dict, home_id: int, away_id: int) -> Optional[StandingsContext]:
    """Cups with group stages (World Cup, European Championship, Champions League league phase): positions from the
    group that holds the teams. A few group games say little about league-wide scoring, so no league averages are set."""
    for grp in data.get("standings", []):
        if grp.get("type") != "TOTAL" or not grp.get("group"):
            continue
        table = grp.get("table", [])
        ids = {(r.get("team") or {}).get("id") for r in table}
        if home_id in ids or away_id in ids:
            return StandingsContext(competition=(data.get("competition") or {}).get("name"), table_size=len(table),
                                    home_team_overall=_row(table, home_id), away_team_overall=_row(table, away_id))
    return None


def extract_standings(data: dict, home_id: int, away_id: int) -> Optional[StandingsContext]:
    by_type = {s.get("type"): s.get("table", []) for s in data.get("standings", []) if not s.get("group")}
    total = by_type.get("TOTAL") or []
    if not total:
        return _group_standings(data, home_id, away_id)
    home_tbl, away_tbl = by_type.get("HOME") or [], by_type.get("AWAY") or []
    team_games = sum(r.get("playedGames", 0) for r in total)
    total_goals = sum(r.get("goalsFor", 0) for r in total)
    hg, hp = sum(r.get("goalsFor", 0) for r in home_tbl), sum(r.get("playedGames", 0) for r in home_tbl)
    ag, ap = sum(r.get("goalsFor", 0) for r in away_tbl), sum(r.get("playedGames", 0) for r in away_tbl)
    return StandingsContext(
        competition=(data.get("competition") or {}).get("name"),
        table_size=len(total),
        league_games=team_games // 2,
        league_total_goals=total_goals,
        league_avg_goals_per_team=round(total_goals / team_games, 4) if team_games else None,
        league_avg_home_goals=round(hg / hp, 4) if hp else None,
        league_avg_away_goals=round(ag / ap, 4) if ap else None,
        league_home_goals_total=hg,
        league_away_goals_total=ag,
        home_team_overall=_row(total, home_id),
        away_team_overall=_row(total, away_id),
        home_team_at_home=_row(by_type.get("HOME") or [], home_id),
        away_team_away=_row(by_type.get("AWAY") or [], away_id),
    )
