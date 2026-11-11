from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


def _lower(v: Any) -> Any:
    return v.strip().lower() if isinstance(v, str) else v


def _clean_pct(v: Any) -> Any:
    return float(str(v).strip().rstrip("%")) if isinstance(v, str) else v


# ================================================================ Agent 1: statistical researcher
class StatFinding(_Lenient):
    statement: str
    source_id: Optional[str] = None  # must refer to a source id supplied by the backend
    reliability: Literal["high", "medium", "low", "unknown"] = "unknown"

    _n = field_validator("reliability", mode="before")(_lower)


def _findings(v: Any) -> Any:
    return [{"statement": x} if isinstance(x, str) else x for x in v] if isinstance(v, list) else v


class PlayerAvailability(_Lenient):
    status: Literal["verified", "partial", "unavailable"] = "unavailable"
    injuries: list[str] = []
    suspensions: list[str] = []
    confirmed_lineup: Optional[str] = None
    notes: str = ""

    _n = field_validator("status", mode="before")(_lower)


class TeamResearch(_Lenient):
    name: str
    league_position: Optional[int] = None
    recent_form: str
    offensive_stats: dict[str, Any] = {}
    defensive_stats: dict[str, Any] = {}
    home_away_performance: str = ""
    rest_and_congestion: str = ""
    player_availability: PlayerAvailability = PlayerAvailability()


class DataQuality(_Lenient):
    level: Literal["high", "medium", "low"]
    missing_data: list[str] = []
    notes: str = ""

    _n = field_validator("level", mode="before")(_lower)


class ResearcherReport(_Lenient):
    home_team: TeamResearch
    away_team: TeamResearch
    head_to_head: str = ""
    key_findings: list[StatFinding] = []
    advanced_stats_status: str = ""  # xG, xGA, shots, possession: available or not
    data_quality: DataQuality
    uncertainties: list[str] = []

    _f = field_validator("key_findings", mode="before")(_findings)


# ================================================================ Agent 2: football intelligence & news
ClaimType = Literal["confirmed_fact", "credible_report", "opinion", "rumour", "unverified"]


class NewsFinding(_Lenient):
    item_id: str  # must match a news item supplied by the backend
    team: Literal["home", "away", "both"] = "both"
    category: str = "other"
    claim_type: ClaimType = "unverified"
    summary: str
    exact_quote: Optional[str] = None  # only kept if it appears verbatim in the supplied headline
    event_date: Optional[str] = None
    reliability_score: float = Field(default=0.3, ge=0, le=1)
    relevance: str = ""
    uncertainty: Literal["low", "medium", "high"] = "high"
    # filled in by the backend from the supplied item, never by the model
    source: Optional[str] = None
    source_url: Optional[str] = None
    link: Optional[str] = None
    published_at: Optional[str] = None
    source_reliability_class: Optional[str] = None
    quote_verified: Optional[bool] = None

    _c = field_validator("claim_type", "uncertainty", mode="before")(_lower)


class TeamIntel(_Lenient):
    team: str = ""
    summary: str = ""
    morale_and_pressure: str = ""
    availability_news: list[str] = []
    findings: list[NewsFinding] = []


class IntelligenceReport(_Lenient):
    status: Literal["ok", "partial", "unavailable"] = "ok"
    home: TeamIntel = TeamIntel()
    away: TeamIntel = TeamIntel()
    cross_cutting: list[str] = []
    overall_relevance: str = ""
    caveats: list[str] = []
    data_quality: DataQuality = DataQuality(level="low", missing_data=[], notes="")

    _s = field_validator("status", mode="before")(_lower)


# ================================================================ Agent 3: chief predictor
Evidence = Literal["statistical", "intelligence", "tactical", "environmental", "speculation"]
AdjustmentTarget = Literal["home_attack", "away_attack", "home_goals_conceded", "away_goals_conceded", "home_advantage"]


class AdjustmentSpec(_Lenient):
    """A bounded correction to one data-derived parameter. `log_effect` is a natural-log multiplier."""

    target: AdjustmentTarget
    log_effect: float = Field(ge=-1, le=1)
    uncertainty_sd: float = Field(default=0.05, ge=0, le=0.5)
    evidence: Evidence
    justification: str
    source_refs: list[str] = []

    _e = field_validator("evidence", "target", mode="before")(_lower)


class ScenarioSpec(_Lenient):
    name: str
    description: str
    probability: float = Field(ge=0, le=100)  # percent
    probability_basis: str = ""
    assumptions: list[str] = []
    impact_on_teams: str = ""
    home_log_effect: float = Field(default=0.0, ge=-1, le=1)
    away_log_effect: float = Field(default=0.0, ge=-1, le=1)
    uncertainty_sd: float = Field(default=0.05, ge=0, le=0.5)
    evidence: Evidence = "tactical"

    _p = field_validator("probability", mode="before")(_clean_pct)
    _e = field_validator("evidence", mode="before")(_lower)


class OpennessCorrelation(_Lenient):
    """Correlation between a team's own attacking and defensive log-strength (open, end-to-end matches > 0)."""

    value: float = Field(ge=-0.5, le=0.5)
    evidence: Evidence
    justification: str


class ReportAssessment(_Lenient):
    contradictions: list[str] = []
    unsupported_claims: list[str] = []
    accepted_conclusions: list[str] = []
    rejected_conclusions: list[str] = []
    missing_information: list[str] = []
    data_quality_notes: str = ""


class ChiefPlan(_Lenient):
    report_assessment: ReportAssessment
    abstain: bool = False
    abstain_reason: str = ""
    adjustments: list[AdjustmentSpec] = []
    openness_correlation: Optional[OpennessCorrelation] = None
    scenarios: list[ScenarioSpec] = []
    parameter_notes: str = ""


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
    scenario_commentary: str = ""
    parameter_commentary: str = ""
    accepted_conclusions: list[str] = []
    rejected_conclusions: list[str] = []
    contradictions_found: list[str] = []
    unsupported_claims: list[str] = []
    confidence: ConfidenceAssessment
