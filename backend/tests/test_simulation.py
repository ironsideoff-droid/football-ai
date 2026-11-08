import ast
from pathlib import Path

import numpy as np
import pytest

from errors import SimulationError
from schemas.agent_reports import SimulationParams
from simulation import monte_carlo
from simulation.monte_carlo import N_SIMULATIONS, run_simulation, simulate_scores, sanitize_params
from simulation.statistics import summarize


def params(**kw):
    base = dict(home_xg=1.6, away_xg=1.0, uncertainty_sigma=0.2, goal_correlation=0.0, rationale="t")
    return SimulationParams(**{**base, **kw})


def test_exactly_1000_outcomes():
    h, a = simulate_scores(1.5, 1.1, 0.2, 0.0, seed=1)
    assert h.shape == a.shape == (1000,) and N_SIMULATIONS == 1000
    assert run_simulation(params(), seed=1).n_simulations == 1000


def test_no_python_loops_in_simulation_module():
    tree = ast.parse(Path(monte_carlo.__file__).read_text())
    banned = (ast.For, ast.While, ast.AsyncFor, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
    assert not [n for n in ast.walk(tree) if isinstance(n, banned)]


def test_seed_is_reproducible_and_exposed():
    a, b = run_simulation(params(), seed=42), run_simulation(params(), seed=42)
    assert a.top_scorelines == b.top_scorelines and a.seed == 42
    assert a.params_applied.home_xg == 1.6


def test_probabilities_are_consistent():
    r = run_simulation(params(), seed=7)
    assert r.home_win_pct + r.draw_pct + r.away_win_pct == pytest.approx(100, abs=0.2)
    assert r.over_2_5_pct + r.under_2_5_pct == pytest.approx(100, abs=0.1)
    assert r.over_1_5_pct >= r.over_2_5_pct >= r.over_3_5_pct
    assert sum(s.count for s in r.top_scorelines) <= 1000


def test_stronger_home_xg_wins_more():
    strong = run_simulation(params(home_xg=2.6, away_xg=0.6), seed=3)
    weak = run_simulation(params(home_xg=0.7, away_xg=1.8), seed=3)
    assert strong.home_win_pct > 60 > 30 > weak.home_win_pct
    assert strong.avg_home_goals > weak.avg_home_goals


def test_mean_goals_track_input_xg():
    r = run_simulation(params(home_xg=1.4, away_xg=1.2, uncertainty_sigma=0.3), seed=11)
    assert r.avg_home_goals == pytest.approx(1.4, abs=0.2)
    assert r.avg_away_goals == pytest.approx(1.2, abs=0.2)


def test_statistics_on_known_scores():
    h = np.array([2, 0, 1, 3, 0])
    a = np.array([1, 0, 1, 3, 2])
    p = params()
    r = summarize(h, a, p, p, [], 0, "m")
    assert (r.home_win_pct, r.draw_pct, r.away_win_pct) == (20.0, 60.0, 20.0)
    assert r.btts_pct == 60.0 and r.home_clean_sheet_pct == 20.0 and r.away_clean_sheet_pct == 40.0
    assert r.over_2_5_pct == 40.0 and r.under_2_5_pct == 60.0
    assert r.top_scorelines[0].count == 1 and r.goal_distribution["home"][0] == 40.0


def test_params_are_clamped_and_reported():
    applied, warnings = sanitize_params(params(home_xg=9.0, uncertainty_sigma=1.5))
    assert applied.home_xg == 5.0 and applied.uncertainty_sigma == 0.6 and len(warnings) == 2


def test_invalid_params_rejected():
    with pytest.raises(Exception):
        params(home_xg=-1)


def test_simulation_errors_are_wrapped(monkeypatch):
    monkeypatch.setattr(monte_carlo, "simulate_scores", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    with pytest.raises(SimulationError):
        run_simulation(params())
