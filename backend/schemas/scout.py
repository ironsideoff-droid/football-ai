from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class Absence(BaseModel):
    team: Literal["home", "away"]
    player: str
    status: Literal["injured", "suspended", "doubtful", "unknown"] = "unknown"
    detail: Optional[str] = None
    source: str
    url: Optional[str] = None


class TeamAdvanced(BaseModel):
    """Recent-match averages scraped from a public stats site (never estimated)."""
    team: str
    source: str
    url: Optional[str] = None
    matches: int = 0
    xg_for_avg: Optional[float] = None
    xg_against_avg: Optional[float] = None
    shots_avg: Optional[float] = None
    shots_on_target_avg: Optional[float] = None
    possession_avg: Optional[float] = None


class LeagueGoalAverages(BaseModel):
    source: str
    home_goals_avg: float
    away_goals_avg: float
    games: int
