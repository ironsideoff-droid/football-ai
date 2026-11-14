from __future__ import annotations

from typing import Optional

from agents.base import BaseAgent
from agents.prompts import CHIEF_FINAL_SYSTEM, CHIEF_PLAN_SYSTEM
from schemas.agent_reports import ChiefPlan, ChiefPrediction, IntelligenceReport, ResearcherReport
from schemas.match import MatchSummary
from schemas.predictions import SimulationResult
from schemas.sources import DataQualityReport


# Output budgets. Groq counts the reserved output tokens against the tokens-per-minute limit of the request,
# so smaller budgets let the calls fit free-tier limits (8,000 TPM on some models).
PLAN_MAX_TOKENS = 2600
FINAL_MAX_TOKENS = 2200


def _quality(q: Optional[DataQualityReport]) -> Optional[dict]:
    return q.model_dump(exclude={"components"}) if q else None


def digest_research(r: ResearcherReport) -> dict:
    """Short form of the statistical report: the Chief needs the conclusions, not every field."""
    team = lambda t: {"name": t.name, "league_position": t.league_position, "recent_form": t.recent_form,  # noqa: E731
                      "home_away_performance": t.home_away_performance, "rest_and_congestion": t.rest_and_congestion,
                      "availability": t.player_availability.status}
    return {"home": team(r.home_team), "away": team(r.away_team), "head_to_head": r.head_to_head,
            "key_findings": [{"statement": f.statement, "source": f.source_id, "reliability": f.reliability} for f in r.key_findings],
            "advanced_stats": r.advanced_stats_status, "data_quality": r.data_quality.model_dump(), "uncertainties": r.uncertainties}


def digest_intel(i: IntelligenceReport) -> dict:
    """Short form of the intelligence report: findings keep their claim type and reliability, links and quotes are dropped."""
    team = lambda t: {"team": t.team, "summary": t.summary, "morale_and_pressure": t.morale_and_pressure,  # noqa: E731
                      "availability_news": t.availability_news,
                      "findings": [{"id": f.item_id, "claim": f.claim_type, "summary": f.summary, "reliability": f.reliability_score,
                                    "uncertainty": f.uncertainty, "relevance": f.relevance} for f in t.findings]}
    return {"status": i.status, "home": team(i.home), "away": team(i.away), "cross_cutting": i.cross_cutting,
            "overall_relevance": i.overall_relevance, "caveats": i.caveats}


def digest_simulation(sim: SimulationResult) -> dict:
    """Everything the Chief needs to interpret the 50,000 simulations, without matrices, arrays or repeated text."""
    out = sim.model_dump(exclude={"goal_distribution", "disclaimer", "score_matrix_pct", "priors", "config", "applied_adjustments",
                                  "parameter_uncertainty", "uncertainty", "events", "warnings", "top_scorelines", "scenarios"})
    out["top_scorelines"] = [f"{s.score} {s.pct}%" for s in sim.top_scorelines[:6]]
    out["parameter_uncertainty"] = {k: {"mean": v["mean"], "p05": v["p05"], "p95": v["p95"]}
                                    for k, v in sim.parameter_uncertainty.items() if k in ("home_xg", "away_xg", "home_advantage")}
    out["events"] = {k: v for k, v in sim.events.items() if not isinstance(v, list)}
    out["mc_standard_error_pct"] = sim.uncertainty.get("monte_carlo_standard_error_pct")
    out["scenarios"] = [{"name": s.name, "assigned_pct": s.assigned_probability_pct, "impact": s.impact_on_teams,
                         "home_effect": s.home_log_effect, "away_effect": s.away_log_effect, "home_win": s.home_win_pct, "draw": s.draw_pct,
                         "away_win": s.away_win_pct, "goals": [s.avg_home_goals, s.avg_away_goals], "btts": s.btts_pct, "over_2_5": s.over_2_5_pct,
                         "top_scorelines": [f"{x.score} {x.pct}%" for x in s.top_scorelines[:3]]} for s in sim.scenarios]
    return out


def digest_priors(priors: dict) -> dict:
    return {k: ({"mean": v["mean"], "log_sd": v["log_sd"]} if isinstance(v, dict) and "log_sd" in v else v)
            for k, v in priors.items() if k != "notes"} | {"notes": priors.get("notes", [])}


class ChiefPredictorAgent(BaseAgent):
    name = "Chief Predictor"
    system_prompt = CHIEF_PLAN_SYSTEM

    @staticmethod
    def _match(match: MatchSummary) -> dict:
        return {"home": match.home.name, "away": match.away.name, "competition": match.competition, "kickoff_utc": match.utc_date}

    async def plan(self, match: MatchSummary, research: ResearcherReport, intel: IntelligenceReport,
                   quality: Optional[DataQualityReport], priors: dict,
                   deep: Optional[dict] = None) -> ChiefPlan:
        return await self._ask(
            {"match": self._match(match), "statistical_report": digest_research(research), "intelligence_report": digest_intel(intel),
             "data_quality": _quality(quality), "data_derived_priors": digest_priors(priors), "deep_search": deep},
            ChiefPlan, CHIEF_PLAN_SYSTEM, max_tokens=PLAN_MAX_TOKENS, compact_payload=True,
        )

    async def finalize(self, match: MatchSummary, research: ResearcherReport, intel: IntelligenceReport, plan: ChiefPlan,
                       simulation: SimulationResult, quality: Optional[DataQualityReport]) -> ChiefPrediction:
        return await self._ask(
            {"match": self._match(match), "statistical_report": digest_research(research), "intelligence_report": digest_intel(intel),
             "phase1_assessment": plan.report_assessment.model_dump(), "data_derived_priors": digest_priors(simulation.priors),
             "applied_adjustments": [{k: a[k] for k in ("target", "applied_log_effect", "evidence", "status") if k in a}
                                     for a in simulation.applied_adjustments],
             "simulation_results": digest_simulation(simulation), "total_goals_distribution_pct": simulation.goal_distribution["total"][:8],
             "data_quality": _quality(quality)},
            ChiefPrediction, CHIEF_FINAL_SYSTEM, max_tokens=FINAL_MAX_TOKENS, compact_payload=True,
        )
