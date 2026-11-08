"""Vectorized Monte Carlo match simulation (exactly 1,000 scorelines).

No Python loops or comprehensions are used here: every simulated match is
generated at once with NumPy array operations (see tests/test_simulation.py,
which parses this file's AST to enforce that).

Model
-----
For each simulated match the expected goals of each side are perturbed by a
mean-preserving log-normal shock (this represents uncertainty in the inputs),
optionally correlated between the two teams. Goals are then drawn from a
Poisson distribution with the perturbed rates.
"""
from __future__ import annotations

import numpy as np

from errors import SimulationError
from schemas.agent_reports import SimulationParams
from schemas.predictions import SimulationResult
from simulation.params import sanitize_params
from simulation.statistics import summarize

N_SIMULATIONS = 1000
MODEL_DESCRIPTION = (
    "Poisson goals with mean-preserving log-normal uncertainty on each team's expected goals "
    "(optionally correlated); 1,000 vectorized NumPy draws."
)


def simulate_scores(
    home_xg: float, away_xg: float, sigma: float, rho: float, seed: int
) -> tuple[np.ndarray, np.ndarray]:
    """Return two int arrays of length N_SIMULATIONS (home goals, away goals)."""
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((N_SIMULATIONS, 2))
    z[:, 1] = rho * z[:, 0] + np.sqrt(1.0 - rho**2) * z[:, 1]
    rates = np.array([home_xg, away_xg]) * np.exp(sigma * z - 0.5 * sigma**2)
    goals = rng.poisson(rates)
    return goals[:, 0], goals[:, 1]


def run_simulation(params: SimulationParams, seed: int | None = None) -> SimulationResult:
    try:
        applied, warnings = sanitize_params(params)
        seed = int(np.random.SeedSequence().entropy % 2**32) if seed is None else int(seed)
        home, away = simulate_scores(
            applied.home_xg, applied.away_xg, applied.uncertainty_sigma, applied.goal_correlation, seed
        )
        if home.shape[0] != N_SIMULATIONS:  # defensive: the spec demands exactly 1,000
            raise SimulationError(f"Expected {N_SIMULATIONS} simulations, got {home.shape[0]}.")
        return summarize(home, away, params, applied, warnings, seed, MODEL_DESCRIPTION)
    except SimulationError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SimulationError(f"Simulation failed: {exc}") from exc
