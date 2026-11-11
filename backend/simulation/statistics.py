"""Aggregation of the simulated matches into the reported statistics (all NumPy)."""
from __future__ import annotations

from typing import Any, Sequence

import numpy as np

from schemas.predictions import ScenarioResult, Scoreline, SimulationResult
from simulation.scenario_engine import Scenario

DISCLAIMER = (
    "These percentages are statistical estimates produced from the assumptions and data supplied to the simulation. "
    "They are not guaranteed real-world probabilities and have not been shown to beat bookmaker or baseline models."
)
MATRIX = 7  # scores 0..5 and "6+"


def pct(count, n: int, digits: int = 2) -> float:
    return round(100.0 * float(count) / n, digits)


def top_scorelines(h: np.ndarray, a: np.ndarray, k: int = 10) -> list[Scoreline]:
    n = h.shape[0]
    codes = np.minimum(h, 99) * 100 + np.minimum(a, 99)
    values, counts = np.unique(codes, return_counts=True)
    order = np.argsort(-counts, kind="stable")[:k]
    return [Scoreline(score=f"{v // 100}-{v % 100}", count=int(c), pct=pct(c, n))
            for v, c in zip(values[order].tolist(), counts[order].tolist())]


def outcome_block(h: np.ndarray, a: np.ndarray) -> dict[str, float]:
    n = h.shape[0]
    tot = h + a
    return {
        "home_win_pct": pct(np.count_nonzero(h > a), n), "draw_pct": pct(np.count_nonzero(h == a), n),
        "away_win_pct": pct(np.count_nonzero(h < a), n), "btts_pct": pct(np.count_nonzero((h > 0) & (a > 0)), n),
        "over_2_5_pct": pct(np.count_nonzero(tot > 2), n),
    }


def dist_summary(x: np.ndarray) -> dict[str, float]:
    p05, p50, p95 = np.percentile(x, [5, 50, 95])
    return {"mean": round(float(x.mean()), 3), "sd": round(float(x.std()), 3), "p05": round(float(p05), 3),
            "p50": round(float(p50), 3), "p95": round(float(p95), 3)}


def scenario_results(scenarios: Sequence[Scenario], labels: np.ndarray, h: np.ndarray, a: np.ndarray) -> list[ScenarioResult]:
    out = []
    for k, s in enumerate(scenarios):  # <= 8 scenarios; each iteration is vectorised
        mask = labels == k
        hk, ak = h[mask], a[mask]
        if hk.size == 0:
            continue
        out.append(ScenarioResult(
            name=s.name, description=s.description, assigned_probability_pct=round(100 * s.probability, 2),
            simulated_n=int(hk.size), assumptions=s.assumptions, impact_on_teams=s.impact, evidence=s.evidence,
            home_log_effect=round(s.home_log_effect, 3), away_log_effect=round(s.away_log_effect, 3),
            uncertainty_sd=round(s.sd, 3), flags=s.flags, avg_home_goals=round(float(hk.mean()), 2),
            avg_away_goals=round(float(ak.mean()), 2), top_scorelines=top_scorelines(hk, ak, 5), **outcome_block(hk, ak)))
    return out


def summarize(sim: dict[str, np.ndarray], params: dict[str, np.ndarray], lam_h: np.ndarray, lam_a: np.ndarray,
              labels: np.ndarray, scenarios: Sequence[Scenario], meta: dict[str, Any]) -> SimulationResult:
    h, a = sim["home"], sim["away"]
    n = int(h.shape[0])
    tot = h + a
    seg_h, seg_a = sim["seg_home"], sim["seg_away"]
    ht_h, ht_a = sim["ht_home"], sim["ht_away"]
    idx = sim["index"]
    labels = labels[idx]

    matrix = np.bincount(np.minimum(h, MATRIX - 1) * MATRIX + np.minimum(a, MATRIX - 1), minlength=MATRIX**2).reshape(MATRIX, MATRIX)
    size, tsize = int(max(h.max(), a.max())) + 1, int(tot.max()) + 1
    dist = {k: np.round(100 * np.bincount(v, minlength=s) / n, 2).tolist() for k, v, s in
            (("home", h, size), ("away", a, size), ("total", tot, tsize))}
    out = outcome_block(h, a)
    p = np.array([out["home_win_pct"], out["draw_pct"], out["away_win_pct"]]) / 100
    entropy = float(-(p[p > 0] * np.log2(p[p > 0])).sum() / np.log2(3))
    se = {k: round(100 * float(np.sqrt(v * (1 - v) / n)), 3) for k, v in zip(("home", "draw", "away"), p)}

    first_before_30 = (seg_h[:, :2].sum(1) + seg_a[:, :2].sum(1)) > 0
    events = {
        "home_red_card_pct": pct(sim["red_home"].sum(), n), "away_red_card_pct": pct(sim["red_away"].sum(), n),
        "goal_before_30_min_pct": pct(first_before_30.sum(), n),
        "goal_after_75_min_pct": pct(((seg_h[:, 5] + seg_a[:, 5]) > 0).sum(), n),
        "home_leads_ht_pct": pct((ht_h > ht_a).sum(), n), "draw_ht_pct": pct((ht_h == ht_a).sum(), n),
        "away_leads_ht_pct": pct((ht_h < ht_a).sum(), n),
        "trailing_at_ht_but_not_losing_pct": pct((((ht_h < ht_a) & (h >= a)) | ((ht_a < ht_h) & (a >= h))).sum(), n),
        "avg_goals_by_15_min_home": np.round(seg_h.mean(0), 3).tolist(), "avg_goals_by_15_min_away": np.round(seg_a.mean(0), 3).tolist(),
    }
    pu = {
        "home_xg": dist_summary(lam_h), "away_xg": dist_summary(lam_a),
        **{k: dist_summary(v) for k, v in params.items() if k != "lam_h" and k != "lam_a"},
    }
    return SimulationResult(
        n_simulations=n, **meta, parameter_uncertainty=pu,
        input_xg_home=round(float(lam_h.mean()), 3), input_xg_away=round(float(lam_a.mean()), 3),
        avg_home_goals=round(float(h.mean()), 3), avg_away_goals=round(float(a.mean()), 3), avg_total_goals=round(float(tot.mean()), 3),
        home_scores_pct=pct(np.count_nonzero(h > 0), n), away_scores_pct=pct(np.count_nonzero(a > 0), n),
        over_1_5_pct=pct(np.count_nonzero(tot > 1), n), over_3_5_pct=pct(np.count_nonzero(tot > 3), n),
        under_2_5_pct=pct(np.count_nonzero(tot < 3), n), home_clean_sheet_pct=pct(np.count_nonzero(a == 0), n),
        away_clean_sheet_pct=pct(np.count_nonzero(h == 0), n), top_scorelines=top_scorelines(h, a),
        score_matrix_pct=np.round(100 * matrix / n, 2).tolist(), goal_distribution=dist,
        scenarios=scenario_results(scenarios, labels, h, a), events=events,
        uncertainty={"monte_carlo_standard_error_pct": se, "outcome_entropy_normalized": round(entropy, 3),
                     "xg_home_90pct_interval": [pu["home_xg"]["p05"], pu["home_xg"]["p95"]],
                     "xg_away_90pct_interval": [pu["away_xg"]["p05"], pu["away_xg"]["p95"]],
                     "note": "Parameter uncertainty is already integrated into the outcome percentages; the interval shows how widely expected goals varied across simulations."},
        disclaimer=DISCLAIMER, **out,
    )
