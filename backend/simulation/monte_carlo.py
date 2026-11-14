"""Monte Carlo engine: exactly 50,000 vectorised match simulations.

Pipeline (all NumPy, no loop over simulations):
  1. draw 50,000 correlated parameter sets from the data-derived priors (+ bounded adjustments)
  2. assign every simulation to a scenario (exact weights)
  3. apply scenario effects to the expected goals of each simulation
  4. simulate the match minute-block by minute-block (goal_model)
  5. aggregate (statistics)
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

from errors import SimulationError
from schemas.agent_reports import AdjustmentSpec, OpennessCorrelation, ScenarioSpec
from schemas.predictions import SimulationResult
from simulation.goal_model import GoalModelConfig, simulate_goals
from simulation.parameter_engine import (LAMBDA_MAX, LAMBDA_MIN, PriorSet, apply_adjustments, effective_correlation,
                                         sample_parameters)
from simulation.variety import draw_variety
from simulation.scenario_engine import assign_scenarios, normalize_scenarios, scenario_log_effects
from simulation.statistics import summarize

N_SIMULATIONS = 50_000
MODEL_NAMES = {
    "poisson": "Time-aware Poisson with Gamma-posterior parameter uncertainty and scenario mixture",
    "dixon_coles": "Time-aware Poisson + Dixon-Coles low-score correction (importance resampling)",
    "bivariate": "Time-aware bivariate Poisson (shared goal shock) with parameter uncertainty and scenario mixture",
}


def run_simulation(
    priors: PriorSet,
    adjustments: Sequence[AdjustmentSpec] = (),
    scenarios: Sequence[ScenarioSpec] = (),
    openness: Optional[OpennessCorrelation] = None,
    cfg: Optional[GoalModelConfig] = None,
    seed: Optional[int] = None,
) -> SimulationResult:
    cfg = cfg or GoalModelConfig()
    try:
        seed = int(np.random.SeedSequence().entropy % 2**32) if seed is None else int(seed)
        rng = np.random.default_rng(seed)
        effects = apply_adjustments(adjustments)
        rho, rho_warning = effective_correlation(openness)
        params = sample_parameters(rng, N_SIMULATIONS, priors, effects, rho)
        scen, scen_warnings = normalize_scenarios(scenarios)
        labels = assign_scenarios(rng, N_SIMULATIONS, scen)
        eff_h, eff_a = scenario_log_effects(rng, labels, scen)

        variety = draw_variety(rng, N_SIMULATIONS, cfg.chaos) if (cfg.variety and cfg.chaos > 0) else None
        if variety is not None:
            eff_h, eff_a = eff_h + variety.eff_h, eff_a + variety.eff_a
        raw_h, raw_a = params["lam_h"] * np.exp(eff_h), params["lam_a"] * np.exp(eff_a)
        clipped = float(np.mean((raw_h < LAMBDA_MIN) | (raw_h > LAMBDA_MAX) | (raw_a < LAMBDA_MIN) | (raw_a > LAMBDA_MAX)))
        lam_h, lam_a = np.clip(raw_h, LAMBDA_MIN, LAMBDA_MAX), np.clip(raw_a, LAMBDA_MIN, LAMBDA_MAX)
        params = {**params, "lam_h": lam_h, "lam_a": lam_a}

        sim = simulate_goals(rng, lam_h, lam_a, cfg)
        if sim["home"].shape[0] != N_SIMULATIONS or sim["away"].shape[0] != N_SIMULATIONS:
            raise SimulationError(f"Expected exactly {N_SIMULATIONS} simulations, got {sim['home'].shape[0]}.")

        warnings = [*effects.warnings, *scen_warnings, *([rho_warning] if rho_warning else [])]
        warnings.extend([f"{clipped:.1%} of simulations had expected goals clipped to [{LAMBDA_MIN}, {LAMBDA_MAX}]."] * int(clipped > 0.001))
        meta = {
            "model": MODEL_NAMES[cfg.model], "seed": seed, "config": cfg.describe() | {"openness_correlation_applied": rho},
            "priors": priors.to_dict(), "applied_adjustments": effects.report, "warnings": warnings,
        }
        if variety is not None:
            meta["config"] = meta["config"] | {"variety_layer": variety.description}
        return summarize(sim, params, lam_h, lam_a, labels, scen, meta, variety)
    except SimulationError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SimulationError(f"Simulation failed: {exc}") from exc
