from __future__ import annotations

import numpy as np

from schemas.agent_reports import SimulationParams

XG_MIN, XG_MAX = 0.1, 5.0
SIGMA_MAX = 0.6
RHO_MIN, RHO_MAX = -0.5, 0.6

_LIMITS = {
    "home_xg": (XG_MIN, XG_MAX),
    "away_xg": (XG_MIN, XG_MAX),
    "uncertainty_sigma": (0.0, SIGMA_MAX),
    "goal_correlation": (RHO_MIN, RHO_MAX),
}


def sanitize_params(params: SimulationParams) -> tuple[SimulationParams, list[str]]:
    """Clamp the Chief Predictor's parameters into a sane range and report every change."""
    values = params.model_dump()
    warnings: list[str] = []
    for name, (lo, hi) in _LIMITS.items():  # 4 parameters, unrelated to the 1,000 simulations
        clamped = float(np.clip(values[name], lo, hi))
        if clamped != values[name]:
            warnings.append(f"{name} {values[name]} was clamped to {clamped} (allowed {lo}..{hi}).")
        values[name] = clamped
    return SimulationParams(**values), warnings
