"""Time-aware goal model, vectorised across all simulated matches.

The match is split into six 15-minute segments. In every segment each team's goals are Poisson with a rate that
depends on the match state, and the state evolves from segment to segment:

    rate = lambda * w_segment * score_state_multiplier * red_card_multiplier

The only Python loop is over the 6 *time segments* (the score in segment s depends on segment s-1). Every
operation inside it acts on the full arrays of simulated matches at once, so there is no loop over simulations.

Optional extensions, selected by `GoalModelConfig.model` (compare them with /api/backtest before switching):
  poisson      independent Poisson goals (default)
  dixon_coles  low-score correction tau(h, a) applied by importance resampling of the simulated matches
  bivariate    shared Poisson shock added to both teams (positive goal correlation)

Constants below are approximations from the published football-analytics literature, NOT fitted to the supplied
data; they are reported with every result and can be switched off with `dynamics=False`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

import numpy as np

N_SEGMENTS = 6
# Share of a match's goals scored in each 15-minute block (late goals are more frequent). Sums to 1.
SEGMENT_WEIGHTS = np.array([0.142, 0.158, 0.166, 0.166, 0.175, 0.193])
RED_CARD_PER_TEAM_MATCH = 0.045  # approx. 0.09 red cards per top-flight match
RED_OWN_MULT, RED_OPP_MULT = 0.70, 1.30  # scoring-rate change for the team that is down to ten / its opponent
TRAIL_BOOST, LEAD_DAMP, STATE_CAP = 0.06, 0.04, 2
MOMENTUM_BOOST = 0.04  # scoring-rate rise in the segment after a team scored (mixed evidence; kept small)
MOMENTUM_NEUTRALISER = 0.991  # ~ 1 / (1 + 0.04 * P(scored in the previous segment)): keeps the average goal rate unchanged
INMATCH_INJURY_PER_TEAM = 0.10  # a key player is forced off at some point
INJURY_OWN_MULT = 0.94  # score effects per goal of difference, capped at 2 goals


@dataclass(frozen=True)
class GoalModelConfig:
    model: Literal["poisson", "dixon_coles", "bivariate"] = "poisson"
    dynamics: bool = True
    dc_rho: float = -0.10
    bp_share: float = 0.0
    variety: bool = True  # match-style/control regimes (monte_carlo) and in-match momentum + injuries (here)
    chaos: float = 1.0  # 0 = off, 1 = default, up to 2: scales every variety effect

    def describe(self) -> dict[str, Any]:
        d = asdict(self)
        d["segments"] = N_SEGMENTS
        d["segment_weights"] = SEGMENT_WEIGHTS.tolist()
        d["event_assumptions"] = {
            "red_card_per_team_match": RED_CARD_PER_TEAM_MATCH, "red_card_own_rate_multiplier": RED_OWN_MULT,
            "red_card_opponent_rate_multiplier": RED_OPP_MULT, "trailing_boost_per_goal": TRAIL_BOOST,
            "leading_damping_per_goal": LEAD_DAMP, "momentum_boost_after_scoring": MOMENTUM_BOOST * self.chaos if self.variety else 0.0,
            "inmatch_key_injury_per_team": INMATCH_INJURY_PER_TEAM * min(1.0, self.chaos) if self.variety else 0.0, "source": "literature approximations, not fitted to this match's data",
        }
        return d


def dc_tau(h: np.ndarray, a: np.ndarray, lam_h: np.ndarray, lam_a: np.ndarray, rho: float) -> np.ndarray:
    """Dixon-Coles (1997) low-score dependence factor; 1 for every other scoreline."""
    tau = np.ones(h.shape, dtype=float)
    tau = np.where((h == 0) & (a == 0), 1 - lam_h * lam_a * rho, tau)
    tau = np.where((h == 0) & (a == 1), 1 + lam_h * rho, tau)
    tau = np.where((h == 1) & (a == 0), 1 + lam_a * rho, tau)
    tau = np.where((h == 1) & (a == 1), 1 - rho, tau)
    return np.clip(tau, 1e-6, None)


def simulate_goals(rng: np.random.Generator, lam_h: np.ndarray, lam_a: np.ndarray, cfg: GoalModelConfig) -> dict[str, np.ndarray]:
    n = lam_h.shape[0]
    home, away = np.zeros(n, dtype=np.int64), np.zeros(n, dtype=np.int64)
    red_h, red_a = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
    seg_h, seg_a = np.zeros((n, N_SEGMENTS), dtype=np.int64), np.zeros((n, N_SEGMENTS), dtype=np.int64)
    ht_h = ht_a = None
    var = cfg.dynamics and cfg.variety and cfg.chaos > 0
    prev_h = prev_a = np.zeros(n, dtype=bool)
    inj_p = INMATCH_INJURY_PER_TEAM * min(1.0, cfg.chaos)
    inj_h = np.where(rng.random(n) < inj_p, rng.integers(1, N_SEGMENTS, n), N_SEGMENTS + 1) if var else np.full(n, N_SEGMENTS + 1)  # segment of the injury
    inj_a = np.where(rng.random(n) < inj_p, rng.integers(1, N_SEGMENTS, n), N_SEGMENTS + 1) if var else np.full(n, N_SEGMENTS + 1)
    for seg in range(N_SEGMENTS):  # time segments only: each step is vectorised over all simulated matches
        w = SEGMENT_WEIGHTS[seg]
        mult_h = mult_a = np.ones(n)
        if cfg.dynamics:
            diff = np.clip(home - away, -STATE_CAP, STATE_CAP)
            mult_h = np.where(diff < 0, 1 + TRAIL_BOOST * -diff, 1 - LEAD_DAMP * diff)
            mult_a = np.where(diff > 0, 1 + TRAIL_BOOST * diff, 1 + LEAD_DAMP * diff)
            mult_h = mult_h * np.where(red_h, RED_OWN_MULT, 1.0) * np.where(red_a, RED_OPP_MULT, 1.0)
            mult_a = mult_a * np.where(red_a, RED_OWN_MULT, 1.0) * np.where(red_h, RED_OPP_MULT, 1.0)
        if var:
            boost = MOMENTUM_BOOST * cfg.chaos
            mult_h = mult_h * np.where(prev_h, 1 + boost, 1.0) * MOMENTUM_NEUTRALISER * np.where(inj_h <= seg, INJURY_OWN_MULT, 1.0)
            mult_a = mult_a * np.where(prev_a, 1 + boost, 1.0) * MOMENTUM_NEUTRALISER * np.where(inj_a <= seg, INJURY_OWN_MULT, 1.0)
        rate_h, rate_a = lam_h * w * mult_h, lam_a * w * mult_a
        shared = np.zeros(n, dtype=np.int64)
        if cfg.model == "bivariate" and cfg.bp_share > 0:
            common = cfg.bp_share * np.minimum(rate_h, rate_a)
            rate_h, rate_a = rate_h - common, rate_a - common
            shared = rng.poisson(common)
        gh, ga = rng.poisson(rate_h) + shared, rng.poisson(rate_a) + shared
        seg_h[:, seg], seg_a[:, seg] = gh, ga
        home, away = home + gh, away + ga
        prev_h, prev_a = gh > 0, ga > 0
        if cfg.dynamics:
            red_h |= rng.random(n) < RED_CARD_PER_TEAM_MATCH * w
            red_a |= rng.random(n) < RED_CARD_PER_TEAM_MATCH * w
        if seg == 2:
            ht_h, ht_a = home.copy(), away.copy()

    if cfg.model == "dixon_coles":  # importance resampling: P(h, a) is proportional to Poisson(h, a) * tau(h, a)
        weights = dc_tau(home, away, lam_h, lam_a, cfg.dc_rho)
        idx = rng.choice(n, size=n, replace=True, p=weights / weights.sum())
        home, away, ht_h, ht_a = home[idx], away[idx], ht_h[idx], ht_a[idx]  # type: ignore[index]
        seg_h, seg_a, red_h, red_a = seg_h[idx], seg_a[idx], red_h[idx], red_a[idx]
        inj_h, inj_a = inj_h[idx], inj_a[idx]
        resampled = idx
    else:
        resampled = np.arange(n)
    return {"home": home, "away": away, "ht_home": ht_h, "ht_away": ht_a, "seg_home": seg_h, "seg_away": seg_a,  # type: ignore[dict-item]
            "red_home": red_h, "red_away": red_a, "inj_home": inj_h <= N_SEGMENTS - 1, "inj_away": inj_a <= N_SEGMENTS - 1,
            "index": resampled}
