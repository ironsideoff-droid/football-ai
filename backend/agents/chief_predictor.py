from __future__ import annotations

from typing import Optional

from agents.base import BaseAgent
from agents.prompts import CHIEF_FINAL_SYSTEM, CHIEF_PLAN_SYSTEM
from schemas.agent_reports import ChiefPlan, ChiefPrediction, IntelligenceReport, ResearcherReport
from schemas.match import MatchSummary
from schemas.predictions import SimulationResult
from schemas.sources import DataQualityReport


def _quality(q: Optional[DataQualityReport]) -> Optional[dict]:
    return q.model_dump(exclude={"components"}) if q else None


class ChiefPredictorAgent(BaseAgent):
    name = "Chief Predictor"
    system_prompt = CHIEF_PLAN_SYSTEM

    @staticmethod
    def _match(match: MatchSummary) -> dict:
        return {"home": match.home.name, "away": match.away.name, "competition": match.competition, "kickoff_utc": match.utc_date}

    async def plan(self, match: MatchSummary, research: ResearcherReport, intel: IntelligenceReport,
                   quality: Optional[DataQualityReport], priors: dict) -> ChiefPlan:
        return await self._ask(
            {"match": self._match(match), "statistical_report": research.model_dump(), "intelligence_report": intel.model_dump(),
             "data_quality": _quality(quality), "data_derived_priors": priors},
            ChiefPlan, CHIEF_PLAN_SYSTEM,
        )

    async def finalize(self, match: MatchSummary, research: ResearcherReport, intel: IntelligenceReport, plan: ChiefPlan,
                       simulation: SimulationResult, quality: Optional[DataQualityReport]) -> ChiefPrediction:
        sim = simulation.model_dump(exclude={"goal_distribution", "disclaimer", "score_matrix_pct", "priors", "config"})
        sim["parameter_uncertainty"] = {k: simulation.parameter_uncertainty[k] for k in ("home_xg", "away_xg", "home_advantage")
                                         if k in simulation.parameter_uncertainty}
        for s in sim["scenarios"]:
            s.pop("assumptions", None)
        return await self._ask(
            {"match": self._match(match), "statistical_report": research.model_dump(), "intelligence_report": intel.model_dump(),
             "phase1_assessment": plan.report_assessment.model_dump(), "data_derived_priors": simulation.priors,
             "applied_adjustments": simulation.applied_adjustments, "simulation_config": simulation.config,
             "simulation_results": sim, "goal_distribution_total_pct": simulation.goal_distribution["total"],
             "data_quality": _quality(quality)},
            ChiefPrediction, CHIEF_FINAL_SYSTEM,
        )
