import json
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from errors import ImmutablePredictionError, StoreError
from evaluation import backtesting, metrics
from evaluation.store import PredictionStore

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


def rec(ph, pd, pa, h, a, **kw):
    return {"probs": {"home": ph, "draw": pd, "away": pa}, "actual": {"home": h, "away": a}, **kw}


def test_one_x_two_metrics_known_values():
    rows = [rec(.6, .3, .1, 2, 0), rec(.2, .3, .5, 1, 1)]
    assert metrics.accuracy_1x2(rows) == 0.5
    assert metrics.brier_1x2(rows) == pytest.approx(((.16 + .09 + .01) + (.04 + .49 + .25)) / 2)
    assert metrics.log_loss_1x2(rows) == pytest.approx((-math.log(.6) - math.log(.3)) / 2)
    assert metrics.log_loss_1x2([rec(1, 0, 0, 0, 1)]) < 40  # zero probability is clipped, never infinite
    assert metrics.accuracy_1x2([]) is None


def test_binary_markets_scored_separately():
    s = metrics.binary_scores([0.8, 0.3, 0.6], [True, False, False])
    assert s["brier"] == pytest.approx(((.2) ** 2 + .3 ** 2 + .6 ** 2) / 3) and s["accuracy"] == pytest.approx(2 / 3) and s["n"] == 3
    assert metrics.binary_scores([], [])["n"] == 0


def test_lazio_napoli_example_is_assessed_per_market():
    """Prediction 1-2 but the match ends 1-1: BTTS right, one team's goals right, outcome wrong, exact score wrong."""
    r = rec(.25, .25, .50, 1, 1, p_btts=.62, p_over_2_5=.45, p_home_scores=.7, p_away_scores=.8, xg_home=1.0, xg_away=2.0, predicted_score=[1, 2])
    rep = metrics.evaluate([r])
    assert rep["outcome_1x2"]["accuracy"] == 0.0
    assert rep["btts"]["accuracy"] == 1.0 and rep["home_team_scores"]["accuracy"] == 1.0 and rep["away_team_scores"]["accuracy"] == 1.0
    assert rep["goals_mae"] == {"home": 0.0, "away": 1.0, "total": 1.0}
    assert rep["exact_score"]["accuracy"] == 0.0 and rep["over_2_5"]["accuracy"] == 1.0
    assert rep["sample_warning"]


def test_exact_score_probability_and_goal_mae():
    m = np.zeros((7, 7)); m[1, 1] = 0.12
    rep = metrics.exact_score([rec(.3, .3, .4, 1, 1, predicted_score=[1, 1], score_matrix=m.tolist()), rec(.3, .3, .4, 0, 3, predicted_score=[1, 1])])
    assert rep["accuracy"] == 0.5 and rep["mean_probability_of_actual_score"] == pytest.approx(0.12)


def test_calibration_curve_is_calibrated_for_calibrated_forecasts():
    rng = np.random.default_rng(0)
    rows = []
    for _ in range(6000):
        p = rng.dirichlet([4, 2, 3])
        o = rng.choice(3, p=p)
        h, a = [(2, 0), (1, 1), (0, 2)][o]
        rows.append(rec(*p, h, a))
    curve = metrics.calibration_curve(rows)
    assert sum(b["n"] for b in curve) == 18000
    assert all(abs(b["mean_predicted"] - b["observed_frequency"]) < 0.06 for b in curve if b["n"] > 300)
    assert metrics.evaluate(rows)["sample_warning"] is None


def test_roi_simulation():
    assert metrics.roi_simulation([rec(.5, .3, .2, 1, 0)])["available"] is False
    rows = [rec(.6, .25, .15, 2, 0, odds={"home": 2.0, "draw": 4.0, "away": 6.0}), rec(.6, .25, .15, 0, 1, odds={"home": 2.0, "draw": 4.0, "away": 6.0})]
    r = metrics.roi_simulation(rows)
    assert r["bets"] == 2 and r["profit"] == 0.0 and r["roi"] == 0.0  # +1 and -1 on the two home bets


# ---------------------------------------------------------------- immutable store
def record(mid=1, kickoff=NOW + timedelta(days=1)):
    return {"match_id": mid, "kickoff_utc": kickoff.isoformat(), "home": "A", "away": "B", "probs": {"home": .5, "draw": .3, "away": .2}}


def test_prediction_is_written_once_and_never_overwritten(tmp_path):
    s = PredictionStore(tmp_path)
    row = s.save_prediction(record(), now=NOW)
    assert row["record"]["created_at"] == NOW.isoformat(timespec="seconds") and len(row["digest"]) == 64
    with pytest.raises(ImmutablePredictionError, match="already exists"):
        s.save_prediction({**record(), "probs": {"home": .9, "draw": .05, "away": .05}}, now=NOW)
    assert len(s.predictions()) == 1 and s.get_prediction(1)["record"]["probs"]["home"] == .5


def test_predictions_after_kickoff_are_refused(tmp_path):
    s = PredictionStore(tmp_path)
    with pytest.raises(ImmutablePredictionError, match="already started"):
        s.save_prediction(record(kickoff=NOW - timedelta(minutes=1)), now=NOW)
    assert s.predictions() == []


def test_tampering_is_detected_and_excluded(tmp_path):
    s = PredictionStore(tmp_path)
    s.save_prediction(record(1), now=NOW)
    s.save_prediction(record(2), now=NOW)
    s.save_result(1, 2, 0), s.save_result(2, 1, 1)
    lines = s.predictions_file.read_text().splitlines()
    row = json.loads(lines[0]); row["record"]["probs"]["home"] = 0.99
    s.predictions_file.write_text("\n".join([json.dumps(row), lines[1]]) + "\n")
    assert s.verify() == [1]
    assert [r["match_id"] for r in s.joined()] == [2]


def test_results_stored_once_and_joined(tmp_path):
    s = PredictionStore(tmp_path)
    s.save_prediction(record(1), now=NOW)
    assert s.save_result(1, 2, 1) and s.save_result(1, 5, 5) is None
    assert s.get_result(1)["home"] == 2 and s.joined()[0]["actual"] == {"home": 2, "away": 1}
    assert PredictionStore(tmp_path / "empty").joined() == []


def test_store_write_failure_is_a_store_error(tmp_path):
    blocker = tmp_path / "file"; blocker.write_text("x")
    with pytest.raises(StoreError):
        PredictionStore(blocker / "sub").save_prediction(record(), now=NOW)


def test_model_is_not_retrained_by_results(tmp_path):
    """Recording results never touches stored predictions or any model parameter."""
    s = PredictionStore(tmp_path)
    s.save_prediction(record(1), now=NOW)
    before = s.predictions_file.read_text()
    s.save_result(1, 0, 3)
    assert s.predictions_file.read_text() == before


# ---------------------------------------------------------------- backtesting
def synthetic_season(n=380, seed=0, dc=False):
    rng = np.random.default_rng(seed)
    teams = list(range(1, 21))
    att = {t: rng.lognormal(0, .3) for t in teams}
    de = {t: rng.lognormal(0, .3) for t in teams}
    out = []
    for r in range(n):
        h, a = (int(x) for x in rng.choice(teams, 2, replace=False))
        out.append({"status": "FINISHED", "utcDate": f"2026-{1 + r // 60:02d}-{1 + r % 28:02d}T{r % 24:02d}:{r % 60:02d}:00Z",
                    "homeTeam": {"id": h}, "awayTeam": {"id": a},
                    "score": {"fullTime": {"home": int(rng.poisson(1.5 * att[h] * de[a] * 1.15)), "away": int(rng.poisson(1.2 * att[a] * de[h] / 1.15))}}})
    return out


def test_score_matrices_are_valid_distributions():
    for model in backtesting.MODELS:
        m = backtesting.score_matrix(1.6, 1.1, model, rho=-0.1, share=0.1)
        assert m.sum() == pytest.approx(1.0) and (m >= 0).all()
    assert backtesting.outcome_probs(backtesting.score_matrix(1.6, 1.1)).sum() == pytest.approx(1.0)
    p = backtesting.outcome_probs(backtesting.score_matrix(2.0, 0.8))
    assert p[0] > p[1] > 0 and p[0] > p[2]
    biv = backtesting.score_matrix(1.4, 1.4, "bivariate", share=0.3)
    ind = backtesting.score_matrix(1.4, 1.4)
    assert np.trace(biv) > np.trace(ind)  # positive correlation -> more draws


def test_walk_forward_is_out_of_sample_and_reports_models():
    rep = backtesting.walk_forward(synthetic_season())
    assert rep["matches_available"] == 380 and 0 < rep["matches_evaluated"] <= 380 - rep["warmup_matches"]
    assert set(rep["models"]) == {"poisson", "dixon_coles", "bivariate", "baseline"}
    assert all(0.8 < m["log_loss"] < 1.3 for m in rep["models"].values()) and rep["scope"]
    assert rep["recommended_model"] == "poisson"  # data are plain Poisson: complexity must not be recommended
    assert (rep["sample_warning"] is None) == (rep["matches_evaluated"] >= 150)  # warning only for small samples


def test_walk_forward_never_uses_future_matches():
    season = synthetic_season(300)
    base = backtesting.walk_forward(season[:200])
    altered = season[:200] + [{**m, "score": {"fullTime": {"home": 9, "away": 0}}} for m in season[200:]]
    again = backtesting.walk_forward(altered)  # corrupting later matches must not change earlier predictions
    first = backtesting.walk_forward(altered[:200])
    assert first["models"] == base["models"] and again["matches_evaluated"] > first["matches_evaluated"]


def test_complex_model_recommended_only_with_significant_gain():
    per = {n: [(1.0, 0.6, 1)] * 200 for n in (*backtesting.MODELS, "baseline")}
    rng = np.random.default_rng(0)
    per["dixon_coles"] = [(1.0 - 0.05 + float(e), 0.6, 1) for e in rng.normal(0, 0.01, 200)]
    per["bivariate"] = [(1.0 + float(e), 0.6, 1) for e in rng.normal(0, 0.05, 200)]
    rep = backtesting._report(per, 200, 300, {"dixon_coles": -0.1, "bivariate": 0.05}, 60, 8.0)
    assert rep["recommended_model"] == "dixon_coles" and rep["comparisons"]["dixon_coles"]["significantly_better"]
    assert not rep["comparisons"]["bivariate"]["significantly_better"]


def test_too_few_matches_is_handled():
    rep = backtesting.walk_forward(synthetic_season(40))
    assert rep["matches_evaluated"] == 0 and rep["recommended_model"] == "poisson" and rep["sample_warning"]


def test_shrinkage_comparison_runs():
    out = backtesting.compare_shrinkage(synthetic_season(200), ks=(2.0, 20.0))
    assert set(out) == {"2.0", "20.0"}
