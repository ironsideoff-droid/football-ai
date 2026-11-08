from agents.base import BaseAgent
from agents.prompts import RESEARCHER_SYSTEM
from schemas.agent_reports import ResearcherReport
from schemas.match import MatchContext


class ResearcherAgent(BaseAgent):
    name = "Researcher"
    system_prompt = RESEARCHER_SYSTEM

    async def run(self, context: MatchContext) -> ResearcherReport:
        return await self._ask(
            {"data": context.to_agent_payload(), "data_availability": context.data_availability},
            ResearcherReport,
        )
