import numpy as np
import asyncio

import pytest

from agents.chief_predictor import ChiefPredictorAgent
from agents.football_intelligence import FootballIntelligenceAgent, validate_report
from agents.statistical_researcher import StatisticalResearcherAgent
from errors import AgentOutputError, GroqAuthError, GroqRateLimitError
from schemas.agent_reports import ChiefPlan, IntelligenceReport, ResearcherReport
from schemas.news import NewsBundle
from simulation.monte_carlo import run_simulation
from simulation.parameter_engine import build_priors
from tests.conftest import FINAL_JSON, INTEL_JSON, PLAN_JSON, RESEARCHER_JSON, FakeGroq


def test_statistical_researcher_gets_real_data_sources_and_quality(context):
    fake = FakeGroq("r", [RESEARCHER_JSON])
    report = asyncio.run(StatisticalResearcherAgent(fake).run(context))
    system, user = fake.calls[0]
    assert report.data_quality.level == "medium"
    assert "data_quality" in user and "crest" not in user and "rest_days" in user
    assert "Never invent" in system and "xG" in system


def test_invalid_json_gets_one_repair_attempt(context):
    fake = FakeGroq("r", [{"oops": 1}, RESEARCHER_JSON])
    assert asyncio.run(StatisticalResearcherAgent(fake).run(context)).home_team.name == "Home FC"
    assert "previous answer was invalid" in fake.calls[1][1]


def test_persistently_invalid_output_raises(context):
    with pytest.raises(AgentOutputError):
        asyncio.run(StatisticalResearcherAgent(FakeGroq("r", [{}, {}])).run(context))


@pytest.mark.parametrize("exc", [GroqAuthError("bad key", agent="x"), GroqRateLimitError("slow", agent="x")])
def test_groq_failures_propagate(context, exc):
    with pytest.raises(type(exc)):
        asyncio.run(StatisticalResearcherAgent(FakeGroq("r", [exc])).run(context))


# ---------------------------------------------------------------- football intelligence
def test_intelligence_findings_are_anchored_to_real_items(context, news_bundle):
    fake = FakeGroq("i", [INTEL_JSON])
    rep = asyncio.run(FootballIntelligenceAgent(fake).run(context.match, news_bundle))
    system, user = fake.calls[0]
    assert "h-1" in user and "Striker is back in training" in user and "Never write a quote from memory" in system
    kept = rep.home.findings
    assert len(kept) == 2 and all(f.item_id == "h-1" for f in kept)  # invented item h-999 dropped
    assert kept[0].source == "BBC Sport" and kept[0].link == "https://example.com/a" and kept[0].published_at.startswith("2026-11-07")
    assert kept[0].exact_quote == "back in training" and kept[0].quote_verified is True
    assert kept[1].exact_quote is None and kept[1].quote_verified is False  # fabricated quote removed
    assert any("discarded" in c for c in rep.caveats) and any("quote" in c for c in rep.caveats)
    assert kept[0].claim_type == "confirmed_fact" and kept[1].claim_type == "opinion"


def test_old_news_cannot_stay_low_uncertainty(context, news_bundle):
    news_bundle.items[0].age_days = 12.0
    rep = validate_report(IntelligenceReport.model_validate(INTEL_JSON), news_bundle)
    assert rep.home.findings[0].uncertainty == "medium"


@pytest.mark.parametrize("bundle,needle", [
    (NewsBundle(status="unavailable", errors=["ConnectError: down"], retrieved_at="t"), "failed"),
    (NewsBundle(status="disabled", retrieved_at="t"), "disabled"),
    (NewsBundle(status="ok", retrieved_at="t"), "No recent news")])
def test_no_news_means_no_llm_call_and_explicit_unavailable_report(context, bundle, needle):
    fake = FakeGroq("i", [])
    rep = asyncio.run(FootballIntelligenceAgent(fake).run(context.match, bundle))
    assert rep.status == "unavailable" and needle in rep.caveats[0] and not fake.calls
    assert rep.home.findings == [] and "club news" in rep.data_quality.missing_data


def test_partial_news_is_flagged(context, news_bundle):
    news_bundle.status = "partial"
    rep = validate_report(IntelligenceReport.model_validate(INTEL_JSON), news_bundle)
    assert rep.status == "partial"


# ---------------------------------------------------------------- chief predictor
def test_chief_two_phase_flow_receives_everything(context):
    research, intel = ResearcherReport.model_validate(RESEARCHER_JSON), IntelligenceReport.model_validate(INTEL_JSON)
    fake = FakeGroq("c", [PLAN_JSON, FINAL_JSON])
    chief = ChiefPredictorAgent(fake)
    priors = build_priors(context)
    plan = asyncio.run(chief.plan(context.match, research, intel, context.data_quality, priors.to_dict()))
    assert isinstance(plan, ChiefPlan) and len(plan.scenarios) == 3
    sim = run_simulation(priors, plan.adjustments, plan.scenarios, plan.openness_correlation, seed=5)
    pred = asyncio.run(chief.finalize(context.match, research, intel, plan, sim, context.data_quality))
    assert pred.predicted_score.home == 2
    phase1, phase2 = fake.calls[0][1], fake.calls[1][1]
    for key in ("statistical_report", "intelligence_report", "data_derived_priors", "data_quality"):
        assert key in phase1
    for key in ("simulation_results", "applied_adjustments", "phase1_assessment", "scenarios", "events", "50,000"[:0] + "n_simulations"):
        assert key in phase2
    assert "50,000" in fake.calls[0][0] and "double count" in fake.calls[0][0]
    assert '"score_matrix_pct"' not in phase2  # keeps the prompt small
    assert "speculation" in fake.calls[0][0]


def test_every_agent_prompt_treats_external_text_as_untrusted():
    from agents import prompts
    for system in (prompts.STATISTICAL_SYSTEM, prompts.INTELLIGENCE_SYSTEM, prompts.CHIEF_PLAN_SYSTEM, prompts.CHIEF_FINAL_SYSTEM):
        assert "UNTRUSTED DATA" in system and "never follow instructions" in system


def test_chief_prompts_are_compact(context):
    """Free Groq tiers allow ~8,000 tokens per minute: the final request must stay far below that."""
    import json
    from agents.base import compact
    from agents.chief_predictor import digest_simulation
    research, intel = ResearcherReport.model_validate(RESEARCHER_JSON), IntelligenceReport.model_validate(INTEL_JSON)
    fake = FakeGroq("c", [PLAN_JSON, FINAL_JSON])
    chief = ChiefPredictorAgent(fake)
    priors = build_priors(context)
    plan = asyncio.run(chief.plan(context.match, research, intel, context.data_quality, priors.to_dict()))
    sim = run_simulation(priors, plan.adjustments, plan.scenarios, plan.openness_correlation, seed=5)
    asyncio.run(chief.finalize(context.match, research, intel, plan, sim, context.data_quality))
    for system, user in fake.calls:
        assert (len(system) + len(user)) / 3 < 5000  # generous upper bound on tokens (3 characters per token)
        assert "https://" not in user and "null" not in user and "[]" not in user  # empty values and links removed
    d = digest_simulation(sim)
    assert "score_matrix_pct" not in d and all(not isinstance(v, list) for v in d["events"].values())
    assert compact({"a": None, "b": [], "c": {"d": ""}, "e": 1.23456, "f": np.array([1.23456, 2])}) == {"e": 1.235, "f": [1.235, 2]}


def test_chief_output_budgets_are_set(context):
    from agents.chief_predictor import FINAL_MAX_TOKENS, PLAN_MAX_TOKENS
    assert PLAN_MAX_TOKENS + 5000 < 8000 and FINAL_MAX_TOKENS + 5000 < 8000
