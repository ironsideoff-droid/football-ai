from agents.base import BaseAgent
from agents.prompts import CHIEF_FINAL_SYSTEM, CHIEF_PLAN_SYSTEM
from schemas.agent_reports import ChiefPlan, ChiefPrediction, ResearcherReport, TacticalReport
from schemas.match import MatchSummary
from schemas.predictions import SimulationResult


class ChiefPredictorAgent(BaseAgent):
    name = "Chief Predictor"
    system_prompt = CHIEF_PLAN_SYSTEM

    async def plan(
        self, match: MatchSummary, research: ResearcherReport, tactical: TacticalReport, availability: dict
    ) -> ChiefPlan:
        return await self._ask(
            {
                "match": {"home": match.home.name, "away": match.away.name, "competition": match.competition,
                          "date": match.utc_date},
                "researcher_report": research.model_dump(),
                "tactical_report": tactical.model_dump(),
                "data_availability": availability,
            },
            ChiefPlan,
            CHIEF_PLAN_SYSTEM,
        )

    async def finalize(
        self,
        match: MatchSummary,
        research: ResearcherReport,
        tactical: TacticalReport,
        plan: ChiefPlan,
        simulation: SimulationResult,
        availability: dict,
    ) -> ChiefPrediction:
        sim = simulation.model_dump(exclude={"goal_distribution", "disclaimer"})
        return await self._ask(
            {
                "match": {"home": match.home.name, "away": match.away.name},
                "researcher_report": research.model_dump(),
                "tactical_report": tactical.model_dump(),
                "phase1_assessment": plan.report_assessment.model_dump(),
                "simulation_input_parameters": simulation.params_applied.model_dump(),
                "simulation_results": sim,
                "goal_distribution_total_pct": simulation.goal_distribution["total"],
                "data_availability": availability,
            },
            ChiefPrediction,
            CHIEF_FINAL_SYSTEM,
        )
