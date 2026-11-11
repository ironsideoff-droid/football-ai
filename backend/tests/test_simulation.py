import ast
import math
import time
from pathlib import Path

import numpy as np
import pytest

from errors import InsufficientDataError, SimulationError
from schemas.agent_reports import AdjustmentSpec, OpennessCorrelation, ScenarioSpec
from simulation import goal_model, monte_carlo, parameter_engine, scenario_engine
from simulation.goal_model import GoalModelConfig, simulate_goals
from simulation.monte_carlo import N_SIMULATIONS, run_simulation
from simulation.parameter_engine import (Prior, PriorSet, apply_adjustments, build_priors, sample_parameters)
from simulation.scenario_engine import allocate_counts, normalize_scenarios


def mk(mean, sd):
    return Prior(math.log(mean) - sd * sd / 2, sd, "test")


@pytest.fixture
def priors():
    return PriorSet(Prior(math.log(1.4), 0.01, "t"), mk(1.2, .15), mk(.9, .15), mk(.95, .15), mk(1.1, .15), Prior(.1, .03, "t"), "season", [])


def scen(name, p, h=0.0, a=0.0, ev="tactical", sd=0.05):
    return ScenarioSpec(name=name, description=f"{name} desc", probability=p, home_log_effect=h, away_log_effect=a, evidence=ev, uncertainty_sd=sd)


# ---------------------------------------------------------------- exactly 50,000, vectorised
def test_exactly_50000_simulations(priors):
    r = run_simulation(priors, seed=1)
    assert N_SIMULATIONS == 50_000 and r.n_simulations == 50_000
    assert sum(r.goal_distribution["home"]) == pytest.approx(100, abs=0.2)
    assert round(sum(np.array(r.score_matrix_pct).ravel()), 1) == pytest.approx(100, abs=0.1)
    assert sum(s.simulated_n for s in r.scenarios) == 50_000


def test_wrong_simulation_count_is_detected(priors, monkeypatch):
    real = monte_carlo.simulate_goals
    monkeypatch.setattr(monte_carlo, "simulate_goals", lambda *a, **k: {**real(*a, **k), "home": np.zeros(10, dtype=int)})
    with pytest.raises(SimulationError, match="50000"):
        run_simulation(priors, seed=1)


def _loops(path, allowed_iter="range(N_SEGMENTS)"):
    tree = ast.parse(Path(path).read_text())
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.While):
            bad.append("while")
        elif isinstance(node, ast.For) and ast.unparse(node.iter) != allowed_iter:
            bad.append(f"for over {ast.unparse(node.iter)}")
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.AsyncFor)):
            bad.append(type(node).__name__)
    return bad


@pytest.mark.parametrize("module", [monte_carlo, goal_model])
def test_no_python_loops_over_simulations(module):
    """The only loop allowed in the simulation core is over the 6 time segments (each step is vectorised)."""
    assert _loops(module.__file__) == []


def test_parameter_and_scenario_sampling_have_no_loops():
    src = Path(parameter_engine.__file__).read_text()
    func = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "sample_parameters")
    assert not [n for n in ast.walk(func) if isinstance(n, (ast.For, ast.While, ast.ListComp, ast.GeneratorExp))]
    for fn in ("assign_scenarios", "scenario_log_effects", "allocate_counts"):
        f = next(n for n in ast.parse(Path(scenario_engine.__file__).read_text()).body if isinstance(n, ast.FunctionDef) and n.name == fn)
        assert not [n for n in ast.walk(f) if isinstance(n, (ast.For, ast.While, ast.ListComp, ast.GeneratorExp))]


def test_fast_enough_to_be_vectorised(priors):
    t = time.time()
    run_simulation(priors, [], [scen("a", 40, 0.1), scen("b", 60)], seed=3)
    assert time.time() - t < 5


def test_seed_reproducible_and_stored(priors):
    a, b, c = run_simulation(priors, seed=42), run_simulation(priors, seed=42), run_simulation(priors, seed=43)
    assert a.seed == 42 and a.top_scorelines == b.top_scorelines and a.home_win_pct == b.home_win_pct
    assert a.home_win_pct != c.home_win_pct or a.top_scorelines != c.top_scorelines


# ---------------------------------------------------------------- dynamic parameters
def test_parameters_differ_between_simulations(priors):
    p = sample_parameters(np.random.default_rng(0), 50_000, priors, apply_adjustments([]))
    for key in ("lam_h", "lam_a", "home_attack", "away_goals_conceded", "home_advantage"):
        assert p[key].std() > 0
    assert len(np.unique(p["lam_h"])) > 49_000
    assert p["lam_h"].mean() == pytest.approx(1.4 * 1.2 * 1.1 * math.exp(0.1), rel=0.04)


def test_sampled_parameter_means_match_priors(priors):
    p = sample_parameters(np.random.default_rng(1), 100_000, priors, apply_adjustments([]))
    assert p["home_attack"].mean() == pytest.approx(1.2, rel=0.01)
    assert p["away_goals_conceded"].mean() == pytest.approx(1.1, rel=0.01)
    assert np.log(p["home_attack"]).std() == pytest.approx(0.15, rel=0.03)


@pytest.mark.parametrize("rho", [-0.3, 0.0, 0.4])
def test_correlated_sampling(priors, rho):
    p = sample_parameters(np.random.default_rng(2), 100_000, priors, apply_adjustments([]), rho)
    c1 = np.corrcoef(np.log(p["home_attack"]), np.log(p["home_goals_conceded"]))[0, 1]
    c2 = np.corrcoef(np.log(p["away_attack"]), np.log(p["away_goals_conceded"]))[0, 1]
    cross = np.corrcoef(np.log(p["home_attack"]), np.log(p["away_attack"]))[0, 1]
    assert c1 == pytest.approx(rho, abs=0.02) and c2 == pytest.approx(rho, abs=0.02) and abs(cross) < 0.02


def test_openness_correlation_needs_evidence(priors):
    r = run_simulation(priors, openness=OpennessCorrelation(value=0.4, evidence="speculation", justification="vibes"), seed=1)
    assert r.config["openness_correlation_applied"] == 0.0 and any("Openness" in w for w in r.warnings)
    r = run_simulation(priors, openness=OpennessCorrelation(value=0.4, evidence="statistical", justification="both teams high-scoring"), seed=1)
    assert r.config["openness_correlation_applied"] == 0.4


def test_adjustments_are_capped_and_speculation_ignored(priors):
    adj = [AdjustmentSpec(target="home_attack", log_effect=0.5, evidence="intelligence", justification="x"),
           AdjustmentSpec(target="away_attack", log_effect=-0.3, evidence="speculation", justification="gut"),
           AdjustmentSpec(target="home_goals_conceded", log_effect=0.1, evidence="statistical", justification="")]
    e = apply_adjustments(adj)
    assert e.mu[parameter_engine.ATT_H] == pytest.approx(0.08) and e.mu[parameter_engine.ATT_A] == 0
    assert e.mu[parameter_engine.CONC_H] == 0
    assert [r["status"] for r in e.report] == ["capped", "ignored", "ignored"]
    assert len(e.warnings) == 3


def test_adjustment_total_per_target_is_limited():
    adj = [AdjustmentSpec(target="home_attack", log_effect=0.15, evidence="statistical", justification="a") for _ in range(3)]
    assert apply_adjustments(adj).mu[parameter_engine.ATT_H] == pytest.approx(0.25)


def test_adjustment_changes_expected_goals(priors):
    base = run_simulation(priors, seed=5)
    up = run_simulation(priors, [AdjustmentSpec(target="home_attack", log_effect=0.12, evidence="statistical", justification="data")], seed=5)
    assert up.input_xg_home > base.input_xg_home * 1.08 and up.applied_adjustments[0]["status"] == "applied"


# ---------------------------------------------------------------- priors from real data
def test_priors_built_from_data(context):
    p = build_priors(context)
    assert p.basis == "season+form"
    assert p.home_attack.mean > 1 > p.away_attack.mean  # home scores 24 in 12, away 14 in 12; league 1.375/game
    assert "standings" in p.home_attack.origin and "recent-form" in p.home_attack.origin
    assert "league home" in p.home_advantage.origin
    xh, xa = p.point_xg()
    assert xh > xa > 0.3


def test_priors_fall_back_to_form_and_flag_assumptions(context):
    context.standings = None
    p = build_priors(context)
    assert p.basis == "form_only" and "ASSUMPTION" in p.home_advantage.origin and p.notes


def test_insufficient_data_raises(context):
    context.standings = None
    context.home_form = None
    with pytest.raises(InsufficientDataError):
        build_priors(context)


def test_more_evidence_means_less_uncertainty():
    few, many = parameter_engine._rate_prior(5, 4, 1.4, "t"), parameter_engine._rate_prior(50, 40, 1.4, "t")
    assert many.sd < few.sd and few.mean == pytest.approx(many.mean, abs=0.1)


# ---------------------------------------------------------------- scenarios
def test_scenario_probabilities_normalise_to_100():
    out, warns = normalize_scenarios([scen("a", 30), scen("b", 20)])
    assert sum(s.probability for s in out) == pytest.approx(1.0) and out[-1].name == scenario_engine.BASELINE_NAME
    assert out[-1].probability == pytest.approx(0.5) and warns
    out, warns = normalize_scenarios([scen("a", 80), scen("b", 80)])
    assert sum(s.probability for s in out) == pytest.approx(1.0) and out[0].probability == pytest.approx(0.5) and warns
    out, _ = normalize_scenarios([])
    assert len(out) == 1 and out[0].probability == 1.0


def test_explicit_events_are_rejected_to_avoid_double_counting():
    out, warns = normalize_scenarios([scen("Red card for the away side", 10, 0.2), scen("Early goal changes the game", 15), scen("Open game", 20)])
    assert [s.name for s in out if s.probability > 0] == ["Open game", scenario_engine.BASELINE_NAME]
    assert sum("double counting" in w for w in warns) == 2


def test_scenario_effects_are_capped_by_evidence():
    out, _ = normalize_scenarios([scen("rain", 10, h=0.9, a=-0.9, ev="speculation")])
    assert out[0].home_log_effect == 0.05 and out[0].away_log_effect == -0.05 and out[0].flags


def test_largest_remainder_allocation_is_exact():
    counts = allocate_counts(np.array([0.333333, 0.333333, 0.333334]), 50_000)
    assert counts.sum() == 50_000
    assert allocate_counts(np.array([0.0001, 0.9999]), 50_000).tolist() == [5, 49_995]


def test_scenarios_weighted_and_conditioned(priors):
    r = run_simulation(priors, scenarios=[scen("Home dominates", 25, 0.15, -0.15, sd=0.0), scen("Rare storm", 2, -0.1, -0.1, "environmental"),
                                          scen("Ordinary match conditions", 73)], seed=9)
    by = {s.name: s for s in r.scenarios}
    assert by["Home dominates"].simulated_n == 12_500 and by["Rare storm"].simulated_n == 1_000 and by["Ordinary match conditions"].simulated_n == 36_500
    assert by["Home dominates"].home_win_pct > by["Ordinary match conditions"].home_win_pct + 5
    assert by["Rare storm"].avg_home_goals + by["Rare storm"].avg_away_goals < by["Ordinary match conditions"].avg_home_goals + by["Ordinary match conditions"].avg_away_goals
    overall = sum(s.home_win_pct * s.simulated_n for s in r.scenarios) / 50_000
    assert overall == pytest.approx(r.home_win_pct, abs=0.05)  # scenario-conditioned results recombine to the total
    assert all(len(s.top_scorelines) <= 5 for s in r.scenarios)


# ---------------------------------------------------------------- outcomes & goal model
def test_outcome_aggregation_is_consistent(priors):
    r = run_simulation(priors, seed=7)
    assert r.home_win_pct + r.draw_pct + r.away_win_pct == pytest.approx(100, abs=0.05)
    assert r.over_2_5_pct + r.under_2_5_pct == pytest.approx(100, abs=0.05)
    assert r.over_1_5_pct >= r.over_2_5_pct >= r.over_3_5_pct
    assert r.btts_pct <= min(r.home_scores_pct, r.away_scores_pct) + 1e-9
    assert r.home_clean_sheet_pct == pytest.approx(100 - r.away_scores_pct, abs=0.01)
    assert r.top_scorelines[0].count >= r.top_scorelines[-1].count
    assert r.avg_total_goals == pytest.approx(r.avg_home_goals + r.avg_away_goals, abs=0.002)
    assert r.uncertainty["monte_carlo_standard_error_pct"]["home"] < 0.3


def test_goal_distribution_matches_poisson_without_dynamics():
    rng = np.random.default_rng(0)
    lam = np.full(50_000, 1.6)
    s = simulate_goals(rng, lam, lam * 0.6, GoalModelConfig(dynamics=False))
    assert s["home"].mean() == pytest.approx(1.6, abs=0.03) and s["away"].mean() == pytest.approx(0.96, abs=0.03)
    assert s["home"].var() == pytest.approx(1.6, rel=0.05)  # Poisson: variance == mean
    assert np.corrcoef(s["home"], s["away"])[0, 1] == pytest.approx(0, abs=0.02)
    assert (s["seg_home"].sum(1) == s["home"]).all() and (s["ht_home"] <= s["home"]).all()


def test_segment_weights_sum_to_one_and_late_goals_more_likely(priors):
    assert goal_model.SEGMENT_WEIGHTS.sum() == pytest.approx(1.0)
    ev = run_simulation(priors, seed=4).events
    assert ev["avg_goals_by_15_min_home"][5] > ev["avg_goals_by_15_min_home"][0]
    assert 0 < ev["home_red_card_pct"] < 10 and ev["home_leads_ht_pct"] + ev["draw_ht_pct"] + ev["away_leads_ht_pct"] == pytest.approx(100, abs=0.05)


def test_dynamics_change_match_state_behaviour():
    lam = np.full(50_000, 1.5)
    on = simulate_goals(np.random.default_rng(1), lam, lam, GoalModelConfig(dynamics=True))
    off = simulate_goals(np.random.default_rng(1), lam, lam, GoalModelConfig(dynamics=False))
    assert on["home"].var() < off["home"].var() * 1.02  # score effects pull outcomes together
    assert on["red_home"].mean() == pytest.approx(0.044, abs=0.006)
    assert not off["red_home"].any()


def test_bivariate_adds_positive_goal_correlation():
    lam = np.full(50_000, 1.4)
    biv = simulate_goals(np.random.default_rng(0), lam, lam, GoalModelConfig(model="bivariate", bp_share=0.3, dynamics=False))
    ind = simulate_goals(np.random.default_rng(0), lam, lam, GoalModelConfig(dynamics=False))
    assert np.corrcoef(biv["home"], biv["away"])[0, 1] > 0.15 > abs(np.corrcoef(ind["home"], ind["away"])[0, 1])
    assert biv["home"].mean() == pytest.approx(1.4, abs=0.04)


def test_dixon_coles_keeps_50000_and_shifts_low_scores():
    lam = np.full(50_000, 1.3)
    base = simulate_goals(np.random.default_rng(0), lam, lam, GoalModelConfig(dynamics=False))
    dc = simulate_goals(np.random.default_rng(0), lam, lam, GoalModelConfig(model="dixon_coles", dc_rho=-0.15, dynamics=False))
    assert dc["home"].shape[0] == 50_000
    p00 = lambda s: np.mean((s["home"] == 0) & (s["away"] == 0))  # noqa: E731
    assert p00(dc) > p00(base)
    assert goal_model.dc_tau(np.array([0, 1, 2]), np.array([0, 1, 2]), np.array([1.0] * 3), np.array([1.0] * 3), -0.1).tolist() == pytest.approx([1.1, 1.1, 1.0])


def test_models_selectable_through_run_simulation(priors):
    for m in ("poisson", "dixon_coles", "bivariate"):
        r = run_simulation(priors, cfg=GoalModelConfig(model=m, bp_share=0.1), seed=2)
        assert r.n_simulations == 50_000 and r.config["model"] == m


def test_extreme_parameters_are_clipped_and_reported():
    wild = PriorSet(Prior(math.log(4.0), 0.01, "t"), mk(3, .1), mk(3, .1), mk(3, .1), mk(3, .1), Prior(.1, .01, "t"), "season", [])
    r = run_simulation(wild, seed=1)
    assert any("clipped" in w for w in r.warnings)


def test_remainder_scenario_never_duplicates_a_name():
    from schemas.agent_reports import ScenarioSpec
    from simulation.scenario_engine import normalize_scenarios
    specs = [ScenarioSpec(name="Ordinary match conditions", description="d", probability=65),
             ScenarioSpec(name="Home dominates", description="d", probability=30, home_log_effect=0.05, evidence="tactical"),
             ScenarioSpec(name="Home dominates", description="again", probability=0)]  # zero-probability entries are dropped
    out, warnings = normalize_scenarios(specs)
    names = [s.name for s in out]
    assert len(names) == len(set(n.lower() for n in names)) == 3
    assert sum(s.probability for s in out) == pytest.approx(1.0) and any("unspecified remainder" in n for n in names)


class _CountingRng:
    """Wraps a numpy Generator and counts how many sampling calls are made."""

    def __init__(self, rng):
        self._rng, self.calls = rng, 0

    def __getattr__(self, name):
        attr = getattr(self._rng, name)
        if not callable(attr):
            return attr

        def wrapped(*args, **kwargs):
            self.calls += 1
            return attr(*args, **kwargs)
        return wrapped


def test_number_of_random_calls_does_not_grow_with_number_of_simulations(priors, monkeypatch):
    """Behavioural proof of vectorisation: 25x more simulated matches must need the same number of NumPy calls."""
    from simulation import monte_carlo
    real, calls = np.random.default_rng, {}
    for n in (2_000, 50_000):
        holder = []
        with monkeypatch.context() as m:
            m.setattr(np.random, "default_rng", lambda seed=None: holder.append(_CountingRng(real(seed))) or holder[-1])
            m.setattr(monte_carlo, "N_SIMULATIONS", n)
            assert monte_carlo.run_simulation(priors, seed=3).n_simulations == n
        calls[n] = holder[0].calls
    assert calls[2_000] == calls[50_000] and calls[50_000] > 0
