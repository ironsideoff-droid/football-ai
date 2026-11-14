"""Built-in variety layer: match-style and control regimes plus per-match random shocks.

Football matches differ in more than the two teams' strengths: some are cagey, some end-to-end, in some one side simply controls the game,
and on the day finishing luck varies. The regimes and shocks below are applied to every one of the 50,000 simulations, independently of the
Chief Predictor's matchup-specific scenarios:

* MATCH STYLE (tempo, both teams): cagey / balanced / open / chaotic, with exact allocation of the 50,000 simulations;
* CONTROL (who dictates play): home control / even / away control, symmetric;
* DAY FORM (each team separately) and TEMPO NOISE: continuous lognormal shocks.

All effects are *mean-preserving*: they are shifted so that the average scoring rate over the regimes equals the data-derived rate. They widen
the spread of outcomes (more 0-0s and more 4-3s) without moving the expected goals. Sizes are literature-style approximations, NOT fitted to
data; `chaos` (0 = off, 1 = default, up to 2) scales every effect and is reported with each result. Whether they improve calibration must
be checked with the backtest before relying on them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from simulation.scenario_engine import Scenario, assign_scenarios

# (name, probability, tempo log-effect on both teams, description)
STYLES = (
    ("Cagey, low-tempo match", 0.22, -0.14, "Few chances, careful play; goals are scarce for both teams."),
    ("Balanced match", 0.46, 0.0, "An ordinary tempo with no particular regime."),
    ("Open, end-to-end match", 0.24, 0.14, "Both teams trade chances; more goals than usual for both."),
    ("Chaotic, high-variance match", 0.08, 0.22, "Errors and transitions dominate; the most goals, the least predictable shape."),
)
# (name, probability, home log-effect, away log-effect, description)
CONTROL = (
    ("Home side controls the game", 0.22, 0.10, -0.10, "The home team dictates play and territory; the away team defends more."),
    ("Even contest", 0.56, 0.0, 0.0, "Neither team clearly dictates play."),
    ("Away side controls the game", 0.22, -0.10, 0.10, "The away team dictates play and territory; the home team defends more."),
)
DAY_FORM_SD = 0.07  # each team's finishing/performance luck on the day (log scale)
TEMPO_NOISE_SD = 0.05  # extra continuous variation of the match tempo


@dataclass
class VarietyDraw:
    eff_h: np.ndarray
    eff_a: np.ndarray
    style_scen: list[Scenario]
    style_labels: np.ndarray
    control_scen: list[Scenario]
    control_labels: np.ndarray
    description: dict


def _neutralise(p: np.ndarray, x: np.ndarray) -> float:
    return float(-np.log(np.sum(p * np.exp(x))))  # shift so that E[exp(effect)] = 1


def draw_variety(rng: np.random.Generator, n: int, chaos: float) -> VarietyDraw:
    chaos = float(np.clip(chaos, 0.0, 2.0))
    sp = np.array([s[1] for s in STYLES])
    st = np.array([s[2] for s in STYLES]) * chaos
    st = st + _neutralise(sp, st)
    cp = np.array([c[1] for c in CONTROL])
    ch = np.array([c[2] for c in CONTROL]) * chaos
    ca = np.array([c[3] for c in CONTROL]) * chaos
    ch, ca = ch + _neutralise(cp, ch), ca + _neutralise(cp, ca)

    style_scen = [Scenario(s[0], s[3], s[1], ["Built-in regime, not fitted to this match"], "Both teams' scoring rate", "model assumption",
                           float(st[i]), float(st[i]), 0.0) for i, s in enumerate(STYLES)]
    control_scen = [Scenario(c[0], c[4], c[1], ["Built-in regime, not fitted to this match"], "Shifts scoring between the teams",
                             "model assumption", float(ch[i]), float(ca[i]), 0.0) for i, c in enumerate(CONTROL)]
    s_lab, c_lab = assign_scenarios(rng, n, style_scen), assign_scenarios(rng, n, control_scen)

    df, tn = DAY_FORM_SD * chaos, TEMPO_NOISE_SD * chaos
    tempo_noise = tn * rng.standard_normal(n) - tn**2 / 2
    eff_h = st[s_lab] + ch[c_lab] + df * rng.standard_normal(n) - df**2 / 2 + tempo_noise
    eff_a = st[s_lab] + ca[c_lab] + df * rng.standard_normal(n) - df**2 / 2 + tempo_noise
    desc = {"chaos": chaos, "styles": [s[0] for s in STYLES], "control_regimes": [c[0] for c in CONTROL], "day_form_sd": df,
            "tempo_noise_sd": tn, "mean_preserving": True, "source": "literature-style approximations, not fitted to this match's data"}
    return VarietyDraw(eff_h, eff_a, style_scen, s_lab, control_scen, c_lab, desc)
