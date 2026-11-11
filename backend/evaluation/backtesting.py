"""Walk-forward (out-of-sample) backtest of the goal models on real finished matches.

For every match after a warm-up, team strengths are estimated ONLY from matches played before it, with the same
Poisson-Gamma shrinkage the live engine uses (no opponent adjustment, no time decay). Three final-score models are
compared on the same matches against a naive baseline that always predicts the historical 1X2 frequencies:

  poisson      independent Poisson goals
  dixon_coles  + low-score correction; rho re-estimated on past matches only
  bivariate    + shared-shock goal correlation; share re-estimated on past matches only

A more complex model is recommended ONLY if its log loss beats plain Poisson by more than two standard errors of
the paired per-match difference. Otherwise the answer is "poisson". This evaluates the final-score models; it does
not evaluate the LLM agents, the news analysis or the scenario engine.
"""
from __future__ import annotations

import math
from typing import Any, Optional, Sequence

import numpy as np

MAX_GOALS = 10
RHO_GRID = np.array([-0.25, -0.20, -0.15, -0.10, -0.05, 0.0, 0.05])
SHARE_GRID = np.array([0.0, 0.05, 0.10, 0.15, 0.20])
REFIT_EVERY = 20
MODELS = ("poisson", "dixon_coles", "bivariate")
_FACT = np.array([math.factorial(i) for i in range(MAX_GOALS + 1)], dtype=float)
_K = np.arange(MAX_GOALS + 1)


def pois(lam: float) -> np.ndarray:
    return np.exp(-lam) * lam**_K / _FACT


def score_matrix(lam_h: float, lam_a: float, model: str = "poisson", rho: float = 0.0, share: float = 0.0) -> np.ndarray:
    """P[h, a] for h, a in 0..MAX_GOALS."""
    if model == "bivariate" and share > 0:
        l3 = share * min(lam_h, lam_a)
        p1, p2, p3 = pois(lam_h - l3), pois(lam_a - l3), pois(l3)
        m = np.zeros((MAX_GOALS + 1, MAX_GOALS + 1))
        for z in range(MAX_GOALS + 1):  # convolution over the shared component (11 terms, not simulations)
            m[z:, z:] += p3[z] * np.outer(p1[: MAX_GOALS + 1 - z], p2[: MAX_GOALS + 1 - z])
        return m / m.sum()
    m = np.outer(pois(lam_h), pois(lam_a))
    if model == "dixon_coles" and rho != 0:
        m[0, 0] *= 1 - lam_h * lam_a * rho
        m[0, 1] *= 1 + lam_h * rho
        m[1, 0] *= 1 + lam_a * rho
        m[1, 1] *= 1 - rho
        m = np.clip(m, 1e-12, None)
    return m / m.sum()


def outcome_probs(m: np.ndarray) -> np.ndarray:
    return np.array([np.tril(m, -1).sum(), np.trace(m), np.triu(m, 1).sum()])  # home, draw, away


def _estimate(state: dict, hid: int, aid: int, k: float) -> Optional[tuple[float, float]]:
    t = state["teams"]
    if state["games"] < 30 or t.get(hid, [0, 0, 0])[2] < 3 or t.get(aid, [0, 0, 0])[2] < 3:
        return None
    g = (state["home_goals"] + state["away_goals"]) / (2 * state["games"])
    h_adv = math.sqrt(max(state["home_goals"], 1) / max(state["away_goals"], 1))

    def rates(tid):
        gf, ga, n = t[tid]
        return ((gf + k * g) / (n + k)) / g, ((ga + k * g) / (n + k)) / g

    att_h, con_h = rates(hid)
    att_a, con_a = rates(aid)
    return g * att_h * con_a * h_adv, g * att_a * con_h / h_adv


def _fit_param(history: list[tuple[float, float, int, int]], model: str) -> float:
    if len(history) < 60 or model == "poisson":
        return 0.0
    grid = RHO_GRID if model == "dixon_coles" else SHARE_GRID
    best, best_ll = 0.0, -np.inf
    for v in grid:  # small parameter grid x past matches; this is model fitting, not match simulation
        ll = 0.0
        for lh, la, h, a in history:
            if h <= MAX_GOALS and a <= MAX_GOALS:
                m = score_matrix(lh, la, model, rho=v, share=v)
                ll += math.log(max(m[h, a], 1e-12))
        if ll > best_ll:
            best, best_ll = float(v), ll
    return best


def walk_forward(matches: Sequence[dict], warmup: int = 60, k: float = 8.0) -> dict[str, Any]:
    rows = [m for m in matches if m.get("status") == "FINISHED" and (m.get("score") or {}).get("fullTime", {}).get("home") is not None]
    rows.sort(key=lambda m: m["utcDate"])
    state: dict[str, Any] = {"teams": {}, "games": 0, "home_goals": 0, "away_goals": 0}
    history: list[tuple[float, float, int, int]] = []
    params = {"dixon_coles": 0.0, "bivariate": 0.0}
    per_model: dict[str, list[tuple[float, float, bool]]] = {m: [] for m in (*MODELS, "baseline")}  # (log loss, brier, hit)
    freq = np.array([1 / 3] * 3)
    counts = np.zeros(3)
    n_eval = 0

    for i, m in enumerate(rows):
        h, a = m["score"]["fullTime"]["home"], m["score"]["fullTime"]["away"]
        hid, aid = m["homeTeam"]["id"], m["awayTeam"]["id"]
        est = _estimate(state, hid, aid, k) if i >= warmup else None
        if est:
            lh, la = est
            actual = 0 if h > a else 1 if h == a else 2
            onehot = np.eye(3)[actual]
            if n_eval % REFIT_EVERY == 0:
                params = {name: _fit_param(history, name) for name in params}
            for name in MODELS:
                mat = score_matrix(lh, la, name, rho=params["dixon_coles"], share=params["bivariate"])
                p = outcome_probs(mat)
                per_model[name].append((-math.log(max(p[actual], 1e-12)), float(((p - onehot) ** 2).sum()), int(p.argmax() == actual)))
            if counts.sum() >= 30:
                freq = counts / counts.sum()
            per_model["baseline"].append((-math.log(max(freq[actual], 1e-12)), float(((freq - onehot) ** 2).sum()), int(freq.argmax() == actual)))
            n_eval += 1
            history.append((lh, la, h, a))
        # learn from the match only after predicting it
        for tid, gf, ga in ((hid, h, a), (aid, a, h)):
            rec = state["teams"].setdefault(tid, [0, 0, 0])
            rec[0], rec[1], rec[2] = rec[0] + gf, rec[1] + ga, rec[2] + 1
        state["games"] += 1
        state["home_goals"] += h
        state["away_goals"] += a
        counts[0 if h > a else 1 if h == a else 2] += 1

    return _report(per_model, n_eval, len(rows), params, warmup, k)


def _report(per_model: dict, n_eval: int, n_total: int, params: dict, warmup: int, k: float) -> dict[str, Any]:
    models = {}
    for name, vals in per_model.items():
        if vals:
            a = np.array(vals, dtype=float)
            models[name] = {"log_loss": round(float(a[:, 0].mean()), 4), "brier": round(float(a[:, 1].mean()), 4), "accuracy": round(float(a[:, 2].mean()), 4)}
    comparisons, recommended, reason = {}, "poisson", "No evaluation possible (too few matches)."
    if n_eval:
        base = np.array([v[0] for v in per_model["poisson"]])
        reason = "Plain Poisson kept: no more complex model beat it by more than 2 standard errors."
        for name in ("dixon_coles", "bivariate"):
            diff = np.array([v[0] for v in per_model[name]]) - base
            se = float(diff.std(ddof=1) / math.sqrt(len(diff))) if len(diff) > 1 else float("nan")
            better = bool(len(diff) > 1 and diff.mean() < -2 * se)
            comparisons[name] = {"mean_log_loss_difference_vs_poisson": round(float(diff.mean()), 5), "standard_error": round(se, 5), "significantly_better": better}
            if better and (recommended == "poisson" or diff.mean() < comparisons[recommended]["mean_log_loss_difference_vs_poisson"]):
                recommended = name
                reason = f"{name} beat plain Poisson by more than 2 standard errors (out-of-sample log loss)."
        base_vs = models["poisson"]["log_loss"] - models["baseline"]["log_loss"]
        comparisons["poisson_vs_baseline_log_loss"] = round(base_vs, 4)
    return {
        "matches_available": n_total, "matches_evaluated": n_eval, "warmup_matches": warmup, "shrinkage_k": k,
        "models": models, "comparisons": comparisons, "recommended_model": recommended, "recommendation_reason": reason,
        "latest_fitted_parameters": {"dc_rho": params["dixon_coles"], "bp_share": params["bivariate"]},
        "sample_warning": None if n_eval >= 150 else f"Only {n_eval} out-of-sample matches: differences between models are likely noise.",
        "scope": "Final-score goal models only; the LLM agents, news analysis and scenarios are not covered by this backtest.",
    }


def compare_shrinkage(matches: Sequence[dict], ks: Sequence[float] = (2.0, 8.0, 20.0)) -> dict[str, Any]:
    out = {}
    for k in ks:
        rep = walk_forward(matches, k=k)
        out[str(k)] = rep["models"].get("poisson")
    return out
