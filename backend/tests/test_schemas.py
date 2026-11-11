import pytest
from pydantic import ValidationError

from schemas.agent_reports import ChiefPlan, ChiefPrediction, IntelligenceReport, ResearcherReport, ScenarioSpec
from tests.conftest import FINAL_JSON, INTEL_JSON, PLAN_JSON, RESEARCHER_JSON


def test_valid_reports_parse_and_normalise_case_and_strings():
    r = ResearcherReport.model_validate(RESEARCHER_JSON)
    assert r.data_quality.level == "medium" and r.key_findings[0].reliability == "high" and r.key_findings[1].statement == "plain string finding"
    i = IntelligenceReport.model_validate(INTEL_JSON)
    assert i.home.findings[0].claim_type == "confirmed_fact" and i.home.findings[0].uncertainty == "low"
    p = ChiefPlan.model_validate(PLAN_JSON)
    assert p.adjustments[0].evidence == "intelligence" and len(p.scenarios) == 3
    assert ChiefPrediction.model_validate(FINAL_JSON).predicted_score.home == 2


@pytest.mark.parametrize("model,payload,drop", [
    (ResearcherReport, RESEARCHER_JSON, "data_quality"), (ChiefPlan, PLAN_JSON, "report_assessment"),
    (ChiefPrediction, FINAL_JSON, "probabilities"), (ChiefPrediction, FINAL_JSON, "confidence")])
def test_missing_required_sections_are_rejected(model, payload, drop):
    with pytest.raises(ValidationError):
        model.model_validate({k: v for k, v in payload.items() if k != drop})


def test_invalid_values_rejected():
    with pytest.raises(ValidationError):
        ChiefPrediction.model_validate({**FINAL_JSON, "probabilities": {"home_win": 150, "draw": 0, "away_win": 0}})
    with pytest.raises(ValidationError):
        ChiefPlan.model_validate({**PLAN_JSON, "adjustments": [{"target": "home_attack", "log_effect": 0.1, "evidence": "magic", "justification": "x"}]})
    with pytest.raises(ValidationError):
        ChiefPlan.model_validate({**PLAN_JSON, "adjustments": [{"target": "referee_bias", "log_effect": 0.1, "evidence": "statistical", "justification": "x"}]})
    assert ScenarioSpec(name="a", description="b", probability="30%").probability == 30
