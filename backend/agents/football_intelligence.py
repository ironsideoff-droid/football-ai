from __future__ import annotations

from agents.base import BaseAgent
from agents.prompts import INTELLIGENCE_SYSTEM
from schemas.agent_reports import DataQuality, IntelligenceReport, TeamIntel
from schemas.match import MatchSummary
from schemas.news import NewsBundle


def unavailable_report(match: MatchSummary, reason: str) -> IntelligenceReport:
    return IntelligenceReport(
        status="unavailable", home=TeamIntel(team=match.home.name), away=TeamIntel(team=match.away.name),
        caveats=[reason], overall_relevance="No usable news was available; off-pitch factors are not part of this analysis.",
        data_quality=DataQuality(level="low", missing_data=["club news", "interviews", "journalist reports"], notes=reason),
    )


def validate_report(report: IntelligenceReport, bundle: NewsBundle) -> IntelligenceReport:
    """Anchor every finding to a real news item: unknown items are dropped, source data is copied from the item,
    and a quote survives only if it appears verbatim in the supplied headline."""
    items = {i.item_id: i for i in bundle.items}
    dropped = quotes_removed = 0
    for side in (report.home, report.away):
        kept = []
        for f in side.findings:
            item = items.get(f.item_id)
            if item is None:
                dropped += 1
                continue
            f.source, f.source_url, f.link = item.source, item.source_url, item.link
            f.published_at, f.source_reliability_class = item.published_at, item.reliability_class
            if f.exact_quote:
                f.quote_verified = f.exact_quote.strip().strip("\"'“”‘’").lower() in item.title.lower()
                if not f.quote_verified:
                    f.exact_quote, quotes_removed = None, quotes_removed + 1
            if item.age_days is not None and item.age_days > 7 and f.uncertainty == "low":
                f.uncertainty = "medium"  # older items cannot be treated as certain about the current situation
            kept.append(f)
        side.findings = kept
    if dropped:
        report.caveats.append(f"{dropped} finding(s) referenced news items that were not supplied and were discarded.")
    if quotes_removed:
        report.caveats.append(f"{quotes_removed} quote(s) did not appear verbatim in the supplied headline and were removed.")
    if bundle.status == "partial":
        report.status = "partial"
        report.caveats.append("News was retrieved for only one team.")
    return report


class FootballIntelligenceAgent(BaseAgent):
    name = "Football Intelligence"
    system_prompt = INTELLIGENCE_SYSTEM

    async def run(self, match: MatchSummary, bundle: NewsBundle) -> IntelligenceReport:
        if not bundle.items:
            reason = "News retrieval is disabled." if bundle.status == "disabled" else (
                "News retrieval failed: " + "; ".join(bundle.errors[:2]) if bundle.errors else "No recent news items were found for either team.")
            return unavailable_report(match, reason)
        payload = {
            "match": {"home": match.home.name, "away": match.away.name, "kickoff_utc": match.utc_date},
            "news_items": [i.model_dump(include={"item_id", "team", "title", "source", "published_at", "age_days", "reliability_class", "origin"})
                           for i in bundle.items],
            "retrieved_at": bundle.retrieved_at,
        }
        return validate_report(await self._ask(payload, IntelligenceReport), bundle)
