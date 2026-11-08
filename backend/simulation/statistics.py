from __future__ import annotations

import numpy as np

from schemas.agent_reports import SimulationParams
from schemas.predictions import Scoreline, SimulationResult

DISCLAIMER = (
    "These percentages are statistical estimates produced from the assumptions and data supplied "
    "to the simulation. They are not guaranteed real-world probabilities."
)


def _pct(count, n: int) -> float:
    return round(100.0 * float(count) / n, 1)


def summarize(
    home: np.ndarray,
    away: np.ndarray,
    requested: SimulationParams,
    applied: SimulationParams,
    warnings: list[str],
    seed: int,
    model: str,
) -> SimulationResult:
    n = int(home.shape[0])
    total = home + away

    codes = np.clip(home, 0, 99) * 100 + np.clip(away, 0, 99)
    values, counts = np.unique(codes, return_counts=True)
    order = np.argsort(-counts, kind="stable")[:10]
    top = [
        Scoreline(score=f"{v // 100}-{v % 100}", count=int(c), pct=_pct(c, n))
        for v, c in zip(values[order].tolist(), counts[order].tolist())
    ]

    size = int(max(home.max(), away.max())) + 1
    total_size = int(total.max()) + 1
    dist = {
        "home": np.round(100 * np.bincount(home, minlength=size) / n, 1).tolist(),
        "away": np.round(100 * np.bincount(away, minlength=size) / n, 1).tolist(),
        "total": np.round(100 * np.bincount(total, minlength=total_size) / n, 1).tolist(),
    }

    return SimulationResult(
        n_simulations=n,
        model=model,
        seed=seed,
        params_requested=requested,
        params_applied=applied,
        param_warnings=warnings,
        home_win_pct=_pct(np.count_nonzero(home > away), n),
        draw_pct=_pct(np.count_nonzero(home == away), n),
        away_win_pct=_pct(np.count_nonzero(home < away), n),
        avg_home_goals=round(float(home.mean()), 2),
        avg_away_goals=round(float(away.mean()), 2),
        avg_total_goals=round(float(total.mean()), 2),
        over_1_5_pct=_pct(np.count_nonzero(total > 1), n),
        over_2_5_pct=_pct(np.count_nonzero(total > 2), n),
        over_3_5_pct=_pct(np.count_nonzero(total > 3), n),
        under_2_5_pct=_pct(np.count_nonzero(total < 3), n),
        btts_pct=_pct(np.count_nonzero((home > 0) & (away > 0)), n),
        home_clean_sheet_pct=_pct(np.count_nonzero(away == 0), n),
        away_clean_sheet_pct=_pct(np.count_nonzero(home == 0), n),
        top_scorelines=top,
        goal_distribution=dist,
        disclaimer=DISCLAIMER,
    )
