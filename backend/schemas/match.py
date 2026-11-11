from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from schemas.sources import DataQualityReport, SourceRecord


class TeamRef(BaseModel):
    id: int
    name: str
    short_name: Optional[str] = None
    tla: Optional[str] = None
    crest: Optional[str] = None


class MatchSummary(BaseModel):
    id: int
    utc_date: str
    status: str
    competition: Optional[str] = None
    competition_code: Optional[str] = None
    matchday: Optional[int] = None
    home: TeamRef
    away: TeamRef
    score_home: Optional[int] = None
    score_away: Optional[int] = None


class RecentMatch(BaseModel):
    date: str
    competition: Optional[str] = None
    opponent: str
    venue: Literal["H", "A"]
    goals_for: int
    goals_against: int
    result: Literal["W", "D", "L"]


class FormSplit(BaseModel):
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    goals_for: int = 0
    goals_against: int = 0


class TeamForm(BaseModel):
    team: str
    matches_considered: int
    overall: FormSplit
    home: FormSplit
    away: FormSplit
    points: int
    points_per_game: Optional[float] = None
    avg_goals_for: Optional[float] = None
    avg_goals_against: Optional[float] = None
    clean_sheets: int = 0
    failed_to_score: int = 0
    sequence_oldest_to_newest: str = ""
    recent_matches: list[RecentMatch] = []
    last_match_date: Optional[str] = None
    rest_days: Optional[float] = None
    matches_last_30_days: int = 0


class H2HSummary(BaseModel):
    matches_considered: int
    upcoming_home_team_wins: int
    draws: int
    upcoming_away_team_wins: int
    upcoming_home_team_goals: int
    upcoming_away_team_goals: int
    recent: list[dict] = []


class StandingRow(BaseModel):
    position: int
    team: str
    played: int
    won: int
    draw: int
    lost: int
    points: int
    goals_for: int
    goals_against: int
    goal_difference: int
    form: Optional[str] = None


class StandingsContext(BaseModel):
    competition: Optional[str] = None
    table_size: int = 0
    league_games: int = 0
    league_total_goals: int = 0
    league_avg_goals_per_team: Optional[float] = None
    league_avg_home_goals: Optional[float] = None
    league_avg_away_goals: Optional[float] = None
    league_home_goals_total: int = 0
    league_away_goals_total: int = 0
    home_team_overall: Optional[StandingRow] = None
    away_team_overall: Optional[StandingRow] = None
    home_team_at_home: Optional[StandingRow] = None
    away_team_away: Optional[StandingRow] = None


class MatchContext(BaseModel):
    match: MatchSummary
    standings: Optional[StandingsContext] = None
    home_form: Optional[TeamForm] = None
    away_form: Optional[TeamForm] = None
    head_to_head: Optional[H2HSummary] = None
    data_availability: dict[str, str] = {}
    sources: list[SourceRecord] = []
    data_quality: Optional[DataQualityReport] = None
    retrieved_at: str

    def to_agent_payload(self) -> dict:
        return self.model_dump(exclude={"match": {"home": {"crest"}, "away": {"crest"}}, "sources": True, "data_quality": True})
