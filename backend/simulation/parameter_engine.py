"""Dynamic parameter engine.

Turns real data into *distributions* over the quantities that drive a match, instead of two fixed expected-goals
values. Every simulated match draws its own parameters.

Model (for one match)
---------------------
    lambda_home = g * ATT_home * CONC_away * H
    lambda_away = g * ATT_away * CONC_home / H

g          league goals per team per game (log-normal, tiny Poisson-based uncertainty)
ATT_x      team x's scoring rate relative to the league average
CONC_x     team x's conceding rate relative to the league average
H          home advantage = sqrt(league home goals / league away goals)

Team rates come from a Poisson-Gamma model: with prior Gamma(k*g, k) (k = SHRINK_K pseudo-games at league
average) and `goals` observed in `games` games, the posterior is Gamma(k*g + goals, k + games). The relative rate
has mean (k*g + goals)/((k + games)*g) and coefficient of variation 1/sqrt(shape). It is sampled as a log-normal
with the same mean and CV (moment matching), which keeps it positive and multiplicative. All draws use one
multivariate normal in log-space so correlations can be imposed explicitly.

Provenance: every prior carries an `origin` string saying which data produced it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np

from errors import InsufficientDataError
from schemas.agent_reports import AdjustmentSpec, OpennessCorrelation
from schemas.match import MatchContext, TeamForm

SHRINK_K = 8.0  # pseudo-games of league-average evidence (regularisation; compare values with /api/backtest)
FORM_WEIGHT = 0.25  # recent matches are already inside the season totals, so only a fraction is added back
FORM_K = 5.0
LEAGUE_MODEL_SD_FLOOR = 0.08  # extra uncertainty when the league average is estimated from the two teams only
FALLBACK_LOG_H = 0.10  # ASSUMPTION used only when league home/away goals are unavailable (flagged in output)
FALLBACK_LOG_H_SD = 0.08
HOME_ADV_SD_FLOOR = 0.02

# Largest |log multiplier| an LLM-proposed adjustment may apply, by kind of evidence. Speculation never moves a parameter.
EVIDENCE_CAPS = {"statistical": 0.15, "intelligence": 0.08, "tactical": 0.10, "environmental": 0.10, "speculation": 0.0}
TARGET_CAP = 0.25  # total |log adjustment| allowed on any one parameter
LAMBDA_MIN, LAMBDA_MAX = 0.05, 6.0

ATT_H, CONC_H, ATT_A, CONC_A, HADV = range(5)
TARGET_INDEX = {"home_attack": ATT_H, "home_goals_conceded": CONC_H, "away_attack": ATT_A,
                "away_goals_conceded": CONC_A, "home_advantage": HADV}


@dataclass
class Prior:
    mu: float  # mean of the log-normal's underlying normal
    sd: float
    origin: str

    @property
    def mean(self) -> float:
        return math.exp(self.mu + self.sd**2 / 2)


@dataclass
class PriorSet:
    log_g: Prior
    home_attack: Prior
    home_conceding: Prior
    away_attack: Prior
    away_conceding: Prior
    home_advantage: Prior
    basis: str
    notes: list[str] = field(default_factory=list)

    def as_list(self) -> list[Prior]:
        return [self.home_attack, self.home_conceding, self.away_attack, self.away_conceding, self.home_advantage]

    def mu_vector(self) -> np.ndarray:
        return np.array([p.mu for p in self.as_list()])

    def sd_vector(self) -> np.ndarray:
        return np.array([p.sd for p in self.as_list()])

    def point_xg(self) -> tuple[float, float]:
        g, h = math.exp(self.log_g.mu), math.exp(self.home_advantage.mu)
        return (g * self.home_attack.mean * self.away_conceding.mean * h,
                g * self.away_attack.mean * self.home_conceding.mean / h)

    def to_dict(self) -> dict[str, Any]:
        xh, xa = self.point_xg()
        row = lambda p: {"mean": round(p.mean, 3), "log_sd": round(p.sd, 3), "origin": p.origin}  # noqa: E731
        return {
            "basis": self.basis,
            "league_goals_per_team_game": round(math.exp(self.log_g.mu), 3),
            "home_attack": row(self.home_attack), "home_goals_conceded": row(self.home_conceding),
            "away_attack": row(self.away_attack), "away_goals_conceded": row(self.away_conceding),
            "home_advantage_multiplier": row(self.home_advantage),
            "point_estimate_xg": {"home": round(xh, 2), "away": round(xa, 2)},
            "notes": self.notes,
        }


def _lognormal_from_mean(mean: float, sigma: float, origin: str) -> Prior:
    return Prior(mu=math.log(mean) - sigma**2 / 2, sd=sigma, origin=origin)


def _rate_prior(goals: float, games: float, league_rate: float, origin: str, k: float = SHRINK_K) -> Prior:
    shape = k * league_rate + goals
    rel_mean = (shape / (k + games)) / league_rate
    return _lognormal_from_mean(rel_mean, math.sqrt(math.log1p(1 / shape)), origin)


def _form_adjust(prior: Prior, recent_goals: float, n: int, season_rate: float, label: str) -> Prior:
    """Blend a fraction of recent form into a season prior (documented shrinkage, avoids double counting)."""
    if n < 3:
        return prior
    weight = FORM_WEIGHT * n / (n + FORM_K)
    ratio = (recent_goals + 0.5) / (n * season_rate + 0.5)
    shift = weight * math.log(ratio)
    sd_form = weight * math.sqrt(1 / (recent_goals + 0.5))
    return Prior(prior.mu + shift, math.sqrt(prior.sd**2 + sd_form**2),
                 f"{prior.origin}; recent-form adjustment {shift:+.3f} ({label}, weight {weight:.2f})")


def build_priors(ctx: MatchContext) -> PriorSet:
    hf, af, st = ctx.home_form, ctx.away_form, ctx.standings
    have_form = bool(hf and af and hf.matches_considered >= 3 and af.matches_considered >= 3)
    hrow, arow = (st.home_team_overall, st.away_team_overall) if st else (None, None)
    have_season = bool(st and hrow and arow and st.league_avg_goals_per_team and st.league_total_goals > 0
                       and hrow.played > 0 and arow.played > 0)
    if not have_season and not have_form:
        raise InsufficientDataError("Not enough real data (no league table and fewer than 3 recent matches for a team) to estimate team strengths.")

    notes: list[str] = []
    if have_season:
        assert st and hrow and arow
        g = st.league_avg_goals_per_team
        log_g = Prior(math.log(g) - 0.5 / st.league_total_goals, math.sqrt(1 / st.league_total_goals),
                      f"standings: {st.league_total_goals} goals in {st.league_games} league games")
        org = lambda r: f"standings season totals ({r.goals_for} scored/{r.goals_against} conceded in {r.played})"  # noqa: E731
        home_att = _rate_prior(hrow.goals_for, hrow.played, g, org(hrow))
        home_con = _rate_prior(hrow.goals_against, hrow.played, g, org(hrow))
        away_att = _rate_prior(arow.goals_for, arow.played, g, org(arow))
        away_con = _rate_prior(arow.goals_against, arow.played, g, org(arow))
        basis = "season"
        if have_form:
            assert hf and af
            home_att = _form_adjust(home_att, hf.overall.goals_for, hf.matches_considered, hrow.goals_for / hrow.played, "home attack")
            home_con = _form_adjust(home_con, hf.overall.goals_against, hf.matches_considered, hrow.goals_against / hrow.played, "home defence")
            away_att = _form_adjust(away_att, af.overall.goals_for, af.matches_considered, arow.goals_for / arow.played, "away attack")
            away_con = _form_adjust(away_con, af.overall.goals_against, af.matches_considered, arow.goals_against / arow.played, "away defence")
            basis = "season+form"
        else:
            notes.append("Recent-form data unavailable: team strengths use season totals only.")
    else:
        assert hf and af
        total = hf.overall.goals_for + hf.overall.goals_against + af.overall.goals_for + af.overall.goals_against
        games = hf.matches_considered + af.matches_considered
        g = total / (2 * games) if total else 1.3
        log_g = Prior(math.log(g), max(LEAGUE_MODEL_SD_FLOOR, math.sqrt(1 / max(total, 1))),
                      f"mean goals per team in the {games} recent matches of the two teams (no league table)")
        org = lambda f: f"last {f.matches_considered} results of {f.team}"  # noqa: E731
        home_att = _rate_prior(hf.overall.goals_for, hf.matches_considered, g, org(hf))
        home_con = _rate_prior(hf.overall.goals_against, hf.matches_considered, g, org(hf))
        away_att = _rate_prior(af.overall.goals_for, af.matches_considered, g, org(af))
        away_con = _rate_prior(af.overall.goals_against, af.matches_considered, g, org(af))
        basis = "form_only"
        notes.append("No league table: league average and team strengths come only from the two teams' recent matches (wider uncertainty).")

    if st and st.league_avg_home_goals and st.league_avg_away_goals and st.league_home_goals_total > 0 and st.league_away_goals_total > 0:
        log_h = 0.5 * math.log(st.league_avg_home_goals / st.league_avg_away_goals)
        sd = math.sqrt(0.25 * (1 / st.league_home_goals_total + 1 / st.league_away_goals_total) + HOME_ADV_SD_FLOOR**2)
        hadv = Prior(log_h, sd, f"league home {st.league_avg_home_goals} vs away {st.league_avg_away_goals} goals per game")
    else:
        hadv = Prior(FALLBACK_LOG_H, FALLBACK_LOG_H_SD, "ASSUMPTION: no league home/away data (not derived from the supplied data)")
        notes.append("Home advantage is an assumption (league home/away goal data unavailable).")
    return PriorSet(log_g, home_att, home_con, away_att, away_con, hadv, basis, notes)


@dataclass
class AdjustmentEffects:
    mu: np.ndarray
    var: np.ndarray
    report: list[dict[str, Any]]
    warnings: list[str]


def apply_adjustments(adjustments: Sequence[AdjustmentSpec]) -> AdjustmentEffects:
    """Bound and record every LLM-proposed adjustment; nothing is applied without evidence and a justification."""
    mu, var = np.zeros(5), np.zeros(5)
    report: list[dict[str, Any]] = []
    warnings: list[str] = []
    for a in adjustments:
        idx = TARGET_INDEX[a.target]
        cap = EVIDENCE_CAPS[a.evidence]
        effect, status, reason = float(a.log_effect), "applied", ""
        if cap == 0.0:
            effect, status, reason = 0.0, "ignored", "speculation may not move a parameter"
        elif not a.justification.strip():
            effect, status, reason = 0.0, "ignored", "no justification supplied"
        elif abs(effect) > cap:
            effect, status, reason = math.copysign(cap, effect), "capped", f"limited to ±{cap} for {a.evidence} evidence"
        total = float(np.clip(mu[idx] + effect, -TARGET_CAP, TARGET_CAP))
        if abs(total - (mu[idx] + effect)) > 1e-12:
            status, reason = "capped", f"{reason + '; ' if reason else ''}target total limited to ±{TARGET_CAP}"
        applied = total - mu[idx]
        sd = max(a.uncertainty_sd, 0.5 * abs(applied)) if status != "ignored" else 0.0
        mu[idx], var[idx] = total, var[idx] + sd**2
        report.append({"target": a.target, "requested_log_effect": a.log_effect, "applied_log_effect": round(applied, 4),
                       "uncertainty_sd": round(sd, 4), "evidence": a.evidence, "status": status, "reason": reason,
                       "justification": a.justification, "source_refs": a.source_refs})
        if status in ("ignored", "capped"):
            warnings.append(f"Adjustment {a.target} ({a.evidence}) {status}: {reason}.")
    return AdjustmentEffects(mu, var, report, warnings)


def effective_correlation(rho: Optional[OpennessCorrelation]) -> tuple[float, Optional[str]]:
    if rho is None:
        return 0.0, None
    if EVIDENCE_CAPS[rho.evidence] == 0.0 or not rho.justification.strip():
        return 0.0, "Openness correlation ignored (speculative or unjustified)."
    return float(np.clip(rho.value, -0.5, 0.5)), None


def sample_parameters(rng: np.random.Generator, n: int, priors: PriorSet, effects: AdjustmentEffects,
                      rho: float = 0.0) -> dict[str, np.ndarray]:
    """Draw n correlated parameter sets (vectorised) and return per-simulation expected goals."""
    mu = priors.mu_vector() + effects.mu
    sd = np.sqrt(priors.sd_vector() ** 2 + effects.var)
    corr = np.eye(5)
    corr[ATT_H, CONC_H] = corr[CONC_H, ATT_H] = rho
    corr[ATT_A, CONC_A] = corr[CONC_A, ATT_A] = rho
    z = rng.standard_normal((n, 5)) @ np.linalg.cholesky(corr).T
    logs = mu + sd * z
    log_g = rng.normal(priors.log_g.mu, priors.log_g.sd, n)
    lam_h = np.exp(log_g + logs[:, ATT_H] + logs[:, CONC_A] + logs[:, HADV])
    lam_a = np.exp(log_g + logs[:, ATT_A] + logs[:, CONC_H] - logs[:, HADV])
    return {
        "lam_h": lam_h, "lam_a": lam_a, "g": np.exp(log_g), "home_attack": np.exp(logs[:, ATT_H]),
        "home_goals_conceded": np.exp(logs[:, CONC_H]), "away_attack": np.exp(logs[:, ATT_A]),
        "away_goals_conceded": np.exp(logs[:, CONC_A]), "home_advantage": np.exp(logs[:, HADV]),
    }
