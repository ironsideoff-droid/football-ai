from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel

from schemas.agent_reports import ChiefPlan, ChiefPrediction, IntelligenceReport, ResearcherReport
from schemas.deep import DeepSearchSummary
from schemas.match import MatchSummary, ViewContext
from schemas.news import NewsItem
from schemas.sources import DataQualityReport, SourceRecord


class Scoreline(BaseModel):
    score: str
    count: int
    pct: float


class ScenarioResult(BaseModel):
    name: str
    description: str
    assigned_probability_pct: float
    simulated_n: int
    assumptions: list[str] = []
    impact_on_teams: str = ""
    evidence: str = ""
    home_log_effect: float = 0.0
    away_log_effect: float = 0.0
    uncertainty_sd: float = 0.0
    flags: list[str] = []
    home_win_pct: float = 0.0
    draw_pct: float = 0.0
    away_win_pct: float = 0.0
    avg_home_goals: float = 0.0
    avg_away_goals: float = 0.0
    btts_pct: float = 0.0
    over_2_5_pct: float = 0.0
    top_scorelines: list[Scoreline] = []


class SimulationResult(BaseModel):
    n_simulations: int
    model: str
    seed: int
    config: dict[str, Any]
    priors: dict[str, Any]
    applied_adjustments: list[dict[str, Any]] = []
    parameter_uncertainty: dict[str, Any]
    warnings: list[str] = []

    home_win_pct: float
    draw_pct: float
    away_win_pct: float
    avg_home_goals: float
    avg_away_goals: float
    avg_total_goals: float
    input_xg_home: float
    input_xg_away: float
    home_scores_pct: float
    away_scores_pct: float
    btts_pct: float
    over_1_5_pct: float
    over_2_5_pct: float
    over_3_5_pct: float
    under_2_5_pct: float
    home_clean_sheet_pct: float
    away_clean_sheet_pct: float
    top_scorelines: list[Scoreline]
    score_matrix_pct: list[list[float]]  # rows = home goals 0..6+, cols = away goals 0..6+
    goal_distribution: dict[str, list[float]]
    scenarios: list[ScenarioResult]
    match_styles: list[ScenarioResult] = []  # built-in tempo regimes (cagey / balanced / open / chaotic)
    control_regimes: list[ScenarioResult] = []  # built-in "who controls the game" regimes
    events: dict[str, Any]
    uncertainty: dict[str, Any]
    disclaimer: str


class AnalysisResult(BaseModel):
    match: MatchSummary
    researcher: Optional[ResearcherReport] = None
    intelligence: Optional[IntelligenceReport] = None
    chief_plan: Optional[ChiefPlan] = None
    simulation: Optional[SimulationResult] = None
    prediction: Optional[ChiefPrediction] = None
    abstained: bool = False
    abstain_reason: str = ""
    data_quality: Optional[DataQualityReport] = None
    sources: list[SourceRecord] = []
    news_items: list[NewsItem] = []
    context: Optional[ViewContext] = None
    deep_search: Optional[DeepSearchSummary] = None
    data_availability: dict[str, str] = {}
    warnings: list[str] = []
    models: dict[str, str] = {}
    prediction_record: dict[str, Any] = {}
    generated_at: str


StepStatus = Literal["pending", "running", "done", "error"]


class StepState(BaseModel):
    key: str
    label: str
    status: StepStatus = "pending"
    message: Optional[str] = None
