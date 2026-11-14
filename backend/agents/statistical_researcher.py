from typing import Optional

from agents.base import BaseAgent
from agents.prompts import STATISTICAL_SYSTEM
from schemas.agent_reports import ResearcherReport
from schemas.match import MatchContext


def source_summary(context: MatchContext) -> list[dict]:
    return [{"id": s.id, "name": s.name, "reliability": s.reliability, "retrieved_at": s.retrieved_at, "status": s.status}
            for s in context.sources if not s.id.startswith(("h-", "a-"))]


class StatisticalResearcherAgent(BaseAgent):
    name = "Statistical Researcher"
    system_prompt = STATISTICAL_SYSTEM

    async def run(self, context: MatchContext, deep: Optional[dict] = None) -> ResearcherReport:
        return await self._ask(
            {
                "data": context.to_agent_payload(),
                "deep_search": deep,
                "sources": source_summary(context),
                "data_quality": context.data_quality.model_dump(exclude={"components"}) if context.data_quality else None,
            },
            ResearcherReport, compact_payload=True,
        )
