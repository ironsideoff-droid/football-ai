"""Evaluation metrics. A prediction record is judged on every market separately, never only on its modal score.

record = {"probs": {"home","draw","away"} (0..1), "p_home_scores", "p_away_scores", "p_btts", "p_over_2_5",
          "xg_home", "xg_away", "predicted_score": [h, a], "score_matrix": 7x7 (0..1) or None,
          "actual": {"home": int, "away": int}, "odds": {"home","draw","away"} optional}
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Optional, Sequence

import numpy as np

EPS = 1e-12
MIN_SAMPLE = 50  # below this, results are shown but flagged as too small to judge


def outcome_of(h: int, a: int) -> str:
    return "home" if h > a else "draw" if h == a else "away"


def accuracy_1x2(records: Sequence[dict]) -> Optional[float]:
    if not records:
        return None
    hits = [max(r["probs"], key=r["probs"].get) == outcome_of(**{"h": r["actual"]["home"], "a": r["actual"]["away"]}) for r in records]
    return float(np.mean(hits))


def brier_1x2(records: Sequence[dict]) -> Optional[float]:
    """Multi-class Brier score (0 = perfect, 2 = worst)."""
    if not records:
        return None
    scores = []
    for r in records:
        o = outcome_of(r["actual"]["home"], r["actual"]["away"])
        scores.append(sum((r["probs"][k] - (1.0 if k == o else 0.0)) ** 2 for k in ("home", "draw", "away")))
    return float(np.mean(scores))


def log_loss_1x2(records: Sequence[dict]) -> Optional[float]:
    if not records:
        return None
    return float(np.mean([-math.log(max(r["probs"][outcome_of(r["actual"]["home"], r["actual"]["away"])], EPS)) for r in records]))


def binary_scores(probs: Sequence[float], outcomes: Sequence[bool]) -> dict[str, Optional[float]]:
    if not len(probs):
        return {"brier": None, "log_loss": None, "accuracy": None, "n": 0}
    p, y = np.clip(np.array(probs, dtype=float), EPS, 1 - EPS), np.array(outcomes, dtype=float)
    return {"brier": float(np.mean((p - y) ** 2)), "log_loss": float(np.mean(-(y * np.log(p) + (1 - y) * np.log(1 - p)))),
            "accuracy": float(np.mean((p >= 0.5) == (y == 1))), "n": int(len(p))}


def goal_mae(records: Sequence[dict]) -> dict[str, Optional[float]]:
    rows = [r for r in records if r.get("xg_home") is not None]
    if not rows:
        return {"home": None, "away": None, "total": None}
    eh = [abs(r["xg_home"] - r["actual"]["home"]) for r in rows]
    ea = [abs(r["xg_away"] - r["actual"]["away"]) for r in rows]
    et = [abs(r["xg_home"] + r["xg_away"] - r["actual"]["home"] - r["actual"]["away"]) for r in rows]
    return {"home": float(np.mean(eh)), "away": float(np.mean(ea)), "total": float(np.mean(et))}


def exact_score(records: Sequence[dict]) -> dict[str, Optional[float]]:
    rows = [r for r in records if r.get("predicted_score")]
    if not rows:
        return {"accuracy": None, "mean_probability_of_actual_score": None}
    hits = [list(r["predicted_score"]) == [r["actual"]["home"], r["actual"]["away"]] for r in rows]
    probs = [r["score_matrix"][r["actual"]["home"]][r["actual"]["away"]] for r in rows
             if r.get("score_matrix") and r["actual"]["home"] < 7 and r["actual"]["away"] < 7]
    return {"accuracy": float(np.mean(hits)), "mean_probability_of_actual_score": float(np.mean(probs)) if probs else None}


def calibration_curve(records: Sequence[dict], bins: int = 10) -> list[dict[str, Any]]:
    """Pooled over the three 1X2 outcome probabilities: predicted probability vs observed frequency per bin."""
    preds, obs = [], []
    for r in records:
        o = outcome_of(r["actual"]["home"], r["actual"]["away"])
        for k in ("home", "draw", "away"):
            preds.append(r["probs"][k])
            obs.append(1.0 if k == o else 0.0)
    if not preds:
        return []
    p, y = np.array(preds), np.array(obs)
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    return [{"bin_low": round(float(edges[b]), 2), "bin_high": round(float(edges[b + 1]), 2),
             "mean_predicted": round(float(p[idx == b].mean()), 4), "observed_frequency": round(float(y[idx == b].mean()), 4),
             "n": int((idx == b).sum())} for b in range(bins) if (idx == b).any()]


def roi_simulation(records: Sequence[dict], min_edge: float = 0.05, stake: float = 1.0) -> dict[str, Any]:
    """Flat-stake ROI on 1X2 bets where model probability * odds exceeds 1 + min_edge. Needs real bookmaker odds."""
    rows = [r for r in records if r.get("odds")]
    if not rows:
        return {"available": False, "reason": "No bookmaker odds stored for evaluated matches."}
    bets = profit = 0.0
    for r in rows:
        o = outcome_of(r["actual"]["home"], r["actual"]["away"])
        for k in ("home", "draw", "away"):
            odds = r["odds"].get(k)
            if odds and r["probs"][k] * odds > 1 + min_edge:
                bets += stake
                profit += stake * (odds - 1) if k == o else -stake
    return {"available": True, "matches_with_odds": len(rows), "bets": int(bets / stake), "profit": round(profit, 2),
            "roi": round(profit / bets, 4) if bets else None, "min_edge": min_edge}


def evaluate(records: Sequence[dict]) -> dict[str, Any]:
    n = len(records)
    both = [r for r in records if r.get("p_btts") is not None]
    over = [r for r in records if r.get("p_over_2_5") is not None]
    hs = [r for r in records if r.get("p_home_scores") is not None]
    as_ = [r for r in records if r.get("p_away_scores") is not None]
    report = {
        "n": n,
        "sample_warning": None if n >= MIN_SAMPLE else f"Only {n} evaluated matches (< {MIN_SAMPLE}): these metrics are too noisy to judge model quality.",
        "outcome_1x2": {"accuracy": accuracy_1x2(records), "brier": brier_1x2(records), "log_loss": log_loss_1x2(records)},
        "home_team_scores": binary_scores([r["p_home_scores"] for r in hs], [r["actual"]["home"] > 0 for r in hs]),
        "away_team_scores": binary_scores([r["p_away_scores"] for r in as_], [r["actual"]["away"] > 0 for r in as_]),
        "btts": binary_scores([r["p_btts"] for r in both], [r["actual"]["home"] > 0 and r["actual"]["away"] > 0 for r in both]),
        "over_2_5": binary_scores([r["p_over_2_5"] for r in over], [r["actual"]["home"] + r["actual"]["away"] > 2 for r in over]),
        "goals_mae": goal_mae(records),
        "exact_score": exact_score(records),
        "calibration": calibration_curve(records),
        "roi": roi_simulation(records),
    }
    return report


def baseline_records(records: Iterable[dict], freq: dict[str, float]) -> list[dict]:
    """Same matches scored with constant 1X2 frequencies: the benchmark any model must beat."""
    return [{**r, "probs": dict(freq)} for r in records]
