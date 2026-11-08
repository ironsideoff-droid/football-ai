import pytest
from pydantic import ValidationError

from schemas.agent_reports import ChiefPlan, ChiefPrediction, ResearcherReport, TacticalReport
from tests.conftest import FINAL_JSON, PLAN_JSON, RESEARCHER_JSON, TACTICAL_JSON


def test_valid_reports_parse_and_normalize_case():
    r = ResearcherReport.model_validate(RESEARCHER_JSON)
    assert r.data_quality.level == "medium" and r.away_team.player_availability.status == "unavailable"
    assert TacticalReport.model_validate(TACTICAL_JSON).scoring_expectations.home_goals == 1.7
    assert ChiefPlan.model_validate(PLAN_JSON).simulation_params.home_xg == 1.65
    assert ChiefPrediction.model_validate(FINAL_JSON).predicted_score.home == 2


@pytest.mark.parametrize("model,payload,drop", [
    (ResearcherReport, RESEARCHER_JSON, "data_quality"),
    (TacticalReport, TACTICAL_JSON, "scoring_expectations"),
    (ChiefPlan, PLAN_JSON, "simulation_params"),
    (ChiefPrediction, FINAL_JSON, "probabilities"),
])
def test_missing_required_sections_are_rejected(model, payload, drop):
    with pytest.raises(ValidationError):
        model.model_validate({k: v for k, v in payload.items() if k != drop})


def test_out_of_range_values_rejected():
    bad = {**FINAL_JSON, "probabilities": {"home_win": 150, "draw": 0, "away_win": 0}}
    with pytest.raises(ValidationError):
        ChiefPrediction.model_validate(bad)
