import asyncio

import pytest

from agents.chief_predictor import ChiefPredictorAgent
from agents.researcher import ResearcherAgent
from agents.tactical_analyst import TacticalAnalystAgent
from errors import AgentOutputError, GroqAuthError
from schemas.agent_reports import ChiefPlan, ResearcherReport, TacticalReport
from simulation.monte_carlo import run_simulation
from tests.conftest import FINAL_JSON, PLAN_JSON, RESEARCHER_JSON, TACTICAL_JSON, FakeGroq


def test_researcher_returns_validated_report_and_sends_availability(context):
    fake = FakeGroq("r", [RESEARCHER_JSON])
    report = asyncio.run(ResearcherAgent(fake).run(context))
    assert report.data_quality.level == "medium"
    assert "injuries_suspensions" in fake.calls[0][1] and "crest" not in fake.calls[0][1]
    assert "Never invent" in fake.calls[0][0]


def test_invalid_json_gets_one_repair_attempt(context):
    fake = FakeGroq("r", [{"oops": 1}, RESEARCHER_JSON])
    assert asyncio.run(ResearcherAgent(fake).run(context)).home_team.name == "Home FC"
    assert "previous answer was invalid" in fake.calls[1][1]


def test_persistently_invalid_output_raises(context):
    with pytest.raises(AgentOutputError):
        asyncio.run(ResearcherAgent(FakeGroq("r", [{}, {}])).run(context))


def test_tactical_optionally_receives_research(context):
    research = ResearcherReport.model_validate(RESEARCHER_JSON)
    f1, f2 = FakeGroq("t", [TACTICAL_JSON]), FakeGroq("t", [TACTICAL_JSON])
    asyncio.run(TacticalAnalystAgent(f1).run(context))
    asyncio.run(TacticalAnalystAgent(f2).run(context, research))
    assert "researcher_report" not in f1.calls[0][1] and "researcher_report" in f2.calls[0][1]


def test_chief_two_phase_flow_receives_everything(context):
    research, tactical = ResearcherReport.model_validate(RESEARCHER_JSON), TacticalReport.model_validate(TACTICAL_JSON)
    fake = FakeGroq("c", [PLAN_JSON, FINAL_JSON])
    chief = ChiefPredictorAgent(fake)
    plan = asyncio.run(chief.plan(context.match, research, tactical, context.data_availability))
    assert isinstance(plan, ChiefPlan)
    sim = run_simulation(plan.simulation_params, seed=5)
    pred = asyncio.run(chief.finalize(context.match, research, tactical, plan, sim, context.data_availability))
    assert pred.predicted_score.home == 2
    phase1, phase2 = fake.calls[0][1], fake.calls[1][1]
    assert "researcher_report" in phase1 and "tactical_report" in phase1
    assert "simulation_results" in phase2 and "simulation_input_parameters" in phase2 and "phase1_assessment" in phase2


def test_groq_errors_propagate(context):
    with pytest.raises(GroqAuthError):
        asyncio.run(ResearcherAgent(FakeGroq("r", [GroqAuthError("bad key", agent="Researcher")])).run(context))
