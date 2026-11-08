from typing import Optional

from agents.base import BaseAgent
from agents.prompts import TACTICAL_SYSTEM
from schemas.agent_reports import ResearcherReport, TacticalReport
from schemas.match import MatchContext


class TacticalAnalystAgent(BaseAgent):
    name = "Tactical Analyst"
    system_prompt = TACTICAL_SYSTEM

    async def run(self, context: MatchContext, research: Optional[ResearcherReport] = None) -> TacticalReport:
        payload = {"data": context.to_agent_payload(), "data_availability": context.data_availability}
        if research is not None:
            payload["researcher_report"] = research.model_dump()
        return await self._ask(payload, TacticalReport)
