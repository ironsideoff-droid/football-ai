from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


def _lower(v: Any) -> Any:
    return v.strip().lower() if isinstance(v, str) else v


# ---------------------------------------------------------------- Agent 1
class PlayerAvailability(_Lenient):
    status: Literal["verified", "partial", "unavailable"] = "unavailable"
    injuries: list[str] = []
    suspensions: list[str] = []
    notes: str = ""

    _n = field_validator("status", mode="before")(_lower)


class TeamResearch(_Lenient):
    name: str
    league_position: Optional[int] = None
    recent_form: str
    offensive_stats: dict[str, Any] = {}
    defensive_stats: dict[str, Any] = {}
    home_away_performance: str = ""
    player_availability: PlayerAvailability = PlayerAvailability()


class DataQuality(_Lenient):
    level: Literal["high", "medium", "low"]
    missing_data: list[str] = []
    notes: str = ""

    _n = field_validator("level", mode="before")(_lower)


class Source(_Lenient):
    name: str
    timestamp: Optional[str] = None


class ResearcherReport(_Lenient):
    home_team: TeamResearch
    away_team: TeamResearch
    head_to_head: str = ""
    key_findings: list[str] = []
    data_quality: DataQuality
    sources: list[Source] = []
    uncertainties: list[str] = []


# ---------------------------------------------------------------- Agent 2
class TacticalProfile(_Lenient):
    style: str
    formation: Optional[str] = None
    strengths: list[str] = []
    weaknesses: list[str] = []
    pressing: str = ""
    counterattacking: str = ""
    possession: str = ""
    defensive_organization: str = ""
    basis: Literal["supplied_data", "model_knowledge", "mixed"] = "mixed"

    _n = field_validator("basis", mode="before")(_lower)


class Scenario(_Lenient):
    name: str
    description: str
    likelihood: Optional[str] = None


class ScoringExpectation(_Lenient):
    home_goals: float = Field(ge=0, le=8)
    away_goals: float = Field(ge=0, le=8)
    reasoning: str


class Confidence(_Lenient):
    level: Literal["low", "medium", "high"]
    uncertainties: list[str] = []

    _n = field_validator("level", mode="before")(_lower)


class TacticalReport(_Lenient):
    home_profile: TacticalProfile
    away_profile: TacticalProfile
    offensive_analysis: str = ""
    defensive_analysis: str = ""
    key_matchups: list[str] = []
    tactical_advantages: list[str] = []
    scenarios: list[Scenario] = []
    expected_match_dynamics: str = ""
    scoring_expectations: ScoringExpectation
    confidence: Confidence


# ---------------------------------------------------------------- Agent 3
class SimulationParams(_Lenient):
    home_xg: float = Field(gt=0, lt=10)
    away_xg: float = Field(gt=0, lt=10)
    uncertainty_sigma: float = Field(default=0.2, ge=0, le=2)
    goal_correlation: float = Field(default=0.0, gt=-1, lt=1)
    rationale: str
    key_inputs: list[str] = []


class ReportAssessment(_Lenient):
    contradictions: list[str] = []
    unsupported_claims: list[str] = []
    accepted_conclusions: list[str] = []
    rejected_conclusions: list[str] = []
    data_quality_notes: str = ""


class ChiefPlan(_Lenient):
    report_assessment: ReportAssessment
    simulation_params: SimulationParams


class Score(_Lenient):
    home: int = Field(ge=0, le=15)
    away: int = Field(ge=0, le=15)


class Probabilities(_Lenient):
    home_win: float = Field(ge=0, le=100)
    draw: float = Field(ge=0, le=100)
    away_win: float = Field(ge=0, le=100)


class OverUnder(_Lenient):
    over_2_5: float = Field(ge=0, le=100)
    under_2_5: float = Field(ge=0, le=100)


class BTTS(_Lenient):
    yes: float = Field(ge=0, le=100)
    no: float = Field(ge=0, le=100)


class ConfidenceAssessment(_Lenient):
    level: Literal["low", "medium", "high"]
    score: int = Field(ge=0, le=100)
    explanation: str
    main_uncertainties: list[str] = []

    _n = field_validator("level", mode="before")(_lower)


class ChiefPrediction(_Lenient):
    predicted_score: Score
    probabilities: Probabilities
    over_under_2_5: OverUnder
    both_teams_to_score: BTTS
    most_likely_scorelines: list[str] = []
    explanation: str
    simulation_interpretation: str = ""
    accepted_conclusions: list[str] = []
    rejected_conclusions: list[str] = []
    contradictions_found: list[str] = []
    unsupported_claims: list[str] = []
    confidence: ConfidenceAssessment
