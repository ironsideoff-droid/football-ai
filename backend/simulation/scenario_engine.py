"""Scenario engine: turns the Chief Predictor's matchup-specific scenarios into weighted regimes.

A scenario is a *regime* that scales the two teams' scoring rates for the whole match (e.g. "home side dominates
possession and the away side sits deep"). Rules enforced here:

* probabilities are percentages of mutually exclusive regimes and are normalised to exactly 100;
* each of the 50,000 simulations belongs to exactly one scenario, allocated by largest-remainder so the weights
  are honoured exactly (rare events stay rare);
* effects are capped by the kind of evidence behind them;
* anything the simulator already generates explicitly (red cards, early/late goals, protecting a lead, chasing an
  equaliser) is rejected as a scenario to avoid double counting. Those appear in the event statistics instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from schemas.agent_reports import ScenarioSpec

MAX_SCENARIOS = 8
SCENARIO_CAPS = {"statistical": 0.20, "intelligence": 0.10, "tactical": 0.20, "environmental": 0.15, "speculation": 0.05}
EXPLICIT_EVENT = re.compile(
    r"red[- ]card|sent[- ]off|early goal|late goal|goal in the (first|last)|protect\w* (a |the )?lead|chas\w* (an |the )?equali[sz]er"
    r"|conced\w* first|go\w* (a goal )?behind|score\w* first", re.I)
BASELINE_NAME = "Ordinary match conditions"


@dataclass
class Scenario:
    name: str
    description: str
    probability: float  # fraction, sums to 1
    assumptions: list[str] = field(default_factory=list)
    impact: str = ""
    evidence: str = "tactical"
    home_log_effect: float = 0.0
    away_log_effect: float = 0.0
    sd: float = 0.0
    flags: list[str] = field(default_factory=list)


def normalize_scenarios(specs: Sequence[ScenarioSpec]) -> tuple[list[Scenario], list[str]]:
    warnings: list[str] = []
    kept: list[ScenarioSpec] = []
    for s in specs:
        if EXPLICIT_EVENT.search(f"{s.name} {s.description[:80]}"):
            warnings.append(f"Scenario '{s.name}' is simulated explicitly as a match event; removed to avoid double counting.")
        elif s.probability > 0:
            kept.append(s)
    if len(kept) > MAX_SCENARIOS:
        kept = sorted(kept, key=lambda s: -s.probability)[:MAX_SCENARIOS]
        warnings.append(f"Only the {MAX_SCENARIOS} most probable scenarios were kept.")

    total = sum(s.probability for s in kept)
    out: list[Scenario] = []
    for s in kept:
        cap = SCENARIO_CAPS[s.evidence]
        flags: list[str] = []
        h, a = float(np.clip(s.home_log_effect, -cap, cap)), float(np.clip(s.away_log_effect, -cap, cap))
        if (h, a) != (s.home_log_effect, s.away_log_effect):
            flags.append(f"effect capped to ±{cap} ({s.evidence} evidence)")
        out.append(Scenario(s.name, s.description, s.probability / 100, s.assumptions, s.impact_on_teams, s.evidence,
                            h, a, max(s.uncertainty_sd, 0.5 * max(abs(h), abs(a))), flags))
    if total < 99.5:
        remainder = (100 - total) / 100
        taken = {x.name.strip().lower() for x in out}
        name = BASELINE_NAME if BASELINE_NAME.lower() not in taken else "Other conditions (unspecified remainder)"
        out.append(Scenario(name, "No distinctive tactical or environmental regime; rates follow the data-derived parameters.",
                            remainder, ["Nothing beyond the data-derived parameters"], "None", "statistical", 0.0, 0.0, 0.0,
                            ["added automatically to complete the probabilities"]))
        warnings.append(f"Scenario probabilities summed to {total:.1f}%; the remaining {100 - total:.1f}% was assigned to '{name}'.")
    elif total > 100.5:
        warnings.append(f"Scenario probabilities summed to {total:.1f}%; normalised to 100%.")
    seen: dict[str, int] = {}
    for sc in out:
        key = sc.name.strip().lower()
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            sc.name = f"{sc.name} ({seen[key]})"
    weights = np.array([s.probability for s in out])
    for s, w in zip(out, weights / weights.sum()):
        s.probability = float(w)
    return out, warnings


def allocate_counts(probs: np.ndarray, n: int) -> np.ndarray:
    """Largest-remainder allocation: integer counts that sum to exactly n."""
    raw = probs * n
    counts = np.floor(raw).astype(int)
    short = n - counts.sum()
    order = np.argsort(-(raw - counts), kind="stable")
    counts[order[:short]] += 1
    return counts


def scenario_vectors(scenarios: Sequence[Scenario]) -> dict[str, np.ndarray]:
    """Per-scenario parameter vectors (at most MAX_SCENARIOS entries) used for vectorised lookups."""
    return {"p": np.array([s.probability for s in scenarios]), "h": np.array([s.home_log_effect for s in scenarios]),
            "a": np.array([s.away_log_effect for s in scenarios]), "sd": np.array([s.sd for s in scenarios])}


def assign_scenarios(rng: np.random.Generator, n: int, scenarios: Sequence[Scenario]) -> np.ndarray:
    counts = allocate_counts(scenario_vectors(scenarios)["p"], n)
    labels = np.repeat(np.arange(counts.shape[0]), counts)
    rng.shuffle(labels)
    return labels


def scenario_log_effects(rng: np.random.Generator, labels: np.ndarray, scenarios: Sequence[Scenario]) -> tuple[np.ndarray, np.ndarray]:
    v = scenario_vectors(scenarios)
    sd = v["sd"][labels]
    return v["h"][labels] + sd * rng.standard_normal(labels.shape[0]), v["a"][labels] + sd * rng.standard_normal(labels.shape[0])
