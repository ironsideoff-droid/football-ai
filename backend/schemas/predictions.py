from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from schemas.agent_reports import (
    ChiefPlan,
    ChiefPrediction,
    ResearcherReport,
    SimulationParams,
    TacticalReport,
)
from schemas.match import MatchSummary


class Scoreline(BaseModel):
    score: str
    count: int
    pct: float


class SimulationResult(BaseModel):
    n_simulations: int
    model: str
    seed: int
    params_requested: SimulationParams
    params_applied: SimulationParams
    param_warnings: list[str] = []
    home_win_pct: float
    draw_pct: float
    away_win_pct: float
    avg_home_goals: float
    avg_away_goals: float
    avg_total_goals: float
    over_1_5_pct: float
    over_2_5_pct: float
    over_3_5_pct: float
    under_2_5_pct: float
    btts_pct: float
    home_clean_sheet_pct: float
    away_clean_sheet_pct: float
    top_scorelines: list[Scoreline]
    goal_distribution: dict[str, list[float]]
    disclaimer: str


class AnalysisResult(BaseModel):
    match: MatchSummary
    researcher: ResearcherReport
    tactical: TacticalReport
    chief_plan: ChiefPlan
    simulation: SimulationResult
    prediction: ChiefPrediction
    data_availability: dict[str, str]
    warnings: list[str] = []
    models: dict[str, str] = {}
    generated_at: str


StepStatus = Literal["pending", "running", "done", "error"]


class StepState(BaseModel):
    key: str
    label: str
    status: StepStatus = "pending"
    message: Optional[str] = None
