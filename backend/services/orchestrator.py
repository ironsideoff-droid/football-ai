from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Callable, Optional

from agents.chief_predictor import ChiefPredictorAgent
from agents.football_intelligence import FootballIntelligenceAgent, unavailable_report
from agents.statistical_researcher import StatisticalResearcherAgent
from errors import AppError, ImmutablePredictionError, InsufficientDataError
from evaluation.store import PredictionStore
from football_data.news import NewsProvider
from football_data.provider import FootballProvider
from football_data.scout import apply_scout
from football_data.source_manager import assess_quality, news_freshness, now_iso
from schemas.agent_reports import ChiefPrediction
from schemas.match import MatchContext, ViewContext
from schemas.news import NewsBundle
from schemas.predictions import AnalysisResult, SimulationResult, StepState
from schemas.sources import SourceRecord
from simulation.goal_model import GoalModelConfig
from simulation.monte_carlo import N_SIMULATIONS, run_simulation
from simulation.parameter_engine import build_priors

log = logging.getLogger("orchestrator")

STEPS = [
    ("data", "Football data"),
    ("news", "News sources"),
    ("scout", "Agent 4: Data Scout (scraping + web research)"),
    ("researcher", "Agent 1: Statistical Researcher"),
    ("intelligence", "Agent 2: Football Intelligence"),
    ("chief_plan", "Agent 3: assessment and scenarios"),
    ("simulation", f"Monte Carlo: {N_SIMULATIONS:,} simulations"),
    ("chief_final", "Agent 3: final prediction"),
]
CONFIDENCE_CAP_BASE = 0.35  # confidence score can never exceed 100 * (0.35 + data-quality score)


class Progress:
    def __init__(self) -> None:
        self.steps = {k: StepState(key=k, label=label) for k, label in STEPS}

    def start(self, key: str, message: Optional[str] = None) -> None:
        self.steps[key].status, self.steps[key].message = "running", message

    def done(self, key: str, message: Optional[str] = None) -> None:
        self.steps[key].status, self.steps[key].message = "done", message

    def fail(self, key: str, message: str) -> None:
        self.steps[key].status, self.steps[key].message = "error", message

    def as_list(self) -> list[dict]:
        return [s.model_dump() for s in self.steps.values()]


def normalize_prediction(pred: ChiefPrediction, sim: SimulationResult) -> list[str]:
    """Make probability groups sum to 100 and flag large departures from the simulation."""
    warnings: list[str] = []

    def norm(values: list[float]) -> list[float]:
        total = sum(values)
        return [round(100 * v / total, 1) for v in values] if total > 0 else values

    p = pred.probabilities
    p.home_win, p.draw, p.away_win = norm([p.home_win, p.draw, p.away_win])
    pred.over_under_2_5.over_2_5, pred.over_under_2_5.under_2_5 = norm([pred.over_under_2_5.over_2_5, pred.over_under_2_5.under_2_5])
    pred.both_teams_to_score.yes, pred.both_teams_to_score.no = norm([pred.both_teams_to_score.yes, pred.both_teams_to_score.no])
    for label, chief, simulated in (("home win", p.home_win, sim.home_win_pct), ("draw", p.draw, sim.draw_pct), ("away win", p.away_win, sim.away_win_pct)):
        if abs(chief - simulated) > 15:
            warnings.append(f"The Chief Predictor's {label} probability ({chief}%) differs from the simulation ({simulated}%) by more than 15 points.")
    return warnings


def cap_confidence(pred: ChiefPrediction, quality_score: float) -> Optional[str]:
    cap = round(100 * min(1.0, CONFIDENCE_CAP_BASE + quality_score))
    c = pred.confidence
    if c.score <= cap:
        return None
    before = c.score
    c.score = cap
    c.level = "low" if cap < 40 else "medium" if cap < 70 else c.level
    return f"Confidence reduced from {before} to {cap} because of limited data quality ({quality_score:.2f})."


def build_record(ctx: MatchContext, sim: SimulationResult, pred: ChiefPrediction, quality_score: float, models: dict) -> dict:
    p = pred.probabilities
    return {
        "match_id": ctx.match.id, "kickoff_utc": ctx.match.utc_date, "home": ctx.match.home.name, "away": ctx.match.away.name,
        "competition": ctx.match.competition,
        "probs": {"home": p.home_win / 100, "draw": p.draw / 100, "away": p.away_win / 100},
        "sim_probs": {"home": sim.home_win_pct / 100, "draw": sim.draw_pct / 100, "away": sim.away_win_pct / 100},
        "p_btts": pred.both_teams_to_score.yes / 100, "sim_p_btts": sim.btts_pct / 100,
        "p_over_2_5": pred.over_under_2_5.over_2_5 / 100, "sim_p_over_2_5": sim.over_2_5_pct / 100,
        "p_home_scores": sim.home_scores_pct / 100, "p_away_scores": sim.away_scores_pct / 100,
        "xg_home": sim.avg_home_goals, "xg_away": sim.avg_away_goals,
        "predicted_score": [pred.predicted_score.home, pred.predicted_score.away],
        "score_matrix": [[round(v / 100, 5) for v in row] for row in sim.score_matrix_pct],
        "confidence": pred.confidence.score, "data_quality_score": quality_score,
        "simulation": {"n": sim.n_simulations, "seed": sim.seed, "model": sim.model}, "models": models,
    }


class Orchestrator:
    def __init__(
        self,
        provider: FootballProvider,
        researcher: StatisticalResearcherAgent,
        intelligence: FootballIntelligenceAgent,
        chief: ChiefPredictorAgent,
        news: Optional[NewsProvider] = None,
        store: Optional[PredictionStore] = None,
        goal_cfg: Optional[GoalModelConfig] = None,
        simulate: Callable = run_simulation,
        web=None,
        scout=None,
        fans=None,
    ):
        self.provider, self.researcher, self.intelligence, self.chief = provider, researcher, intelligence, chief
        self.news, self.store, self.goal_cfg, self.simulate = news, store, goal_cfg or GoalModelConfig(), simulate
        self.web = web  # optional WebResearch
        self.fans = fans  # optional football_data.fans.RedditFans
        self.scout = scout  # optional football_data.scout.Scout

    async def _step(self, progress: Progress, key: str, coro, message: Optional[str] = None):
        progress.start(key, message)
        try:
            result = await coro
        except AppError as exc:
            progress.fail(key, exc.message)
            raise
        except Exception as exc:  # noqa: BLE001
            progress.fail(key, str(exc))
            raise
        progress.done(key)
        return result

    async def _fetch_news(self, ctx: MatchContext) -> NewsBundle:
        if self.news is None:
            bundle = NewsBundle(status="disabled", retrieved_at=now_iso(), provider="disabled")
        else:
            try:
                bundle = await self.news.fetch(ctx.match)
            except Exception as exc:  # noqa: BLE001  news is optional: its failure never stops the analysis
                bundle = NewsBundle(status="unavailable", errors=[f"{type(exc).__name__}: {exc}"], retrieved_at=now_iso())
        if self.web is not None:  # optional deep research; any failure only skips it
            bundle = bundle.model_copy(update={"items": list(bundle.items), "errors": list(bundle.errors)})  # never edit the provider's own object
            try:
                fetch_all = getattr(self.web, "fetch_all", None)
                if fetch_all:
                    found, absences = await asyncio.wait_for(fetch_all(ctx.match), timeout=120)
                    bundle.absences = list(absences)
                else:
                    found = await asyncio.wait_for(self.web.fetch(ctx.match), timeout=120)
                known = {re.sub(r"\W+", " ", i.title.lower()) for i in bundle.items}
                bundle.items = [*bundle.items, *[i for i in found if re.sub(r"\W+", " ", i.title.lower()) not in known]]
                if bundle.status in ("unavailable", "disabled") and found:
                    bundle.status, bundle.provider = "partial", (bundle.provider or "web research")
                bundle.provider = f"{bundle.provider} + web research".strip(" +")
            except Exception as exc:  # noqa: BLE001
                bundle.errors.append(f"Web research skipped: {getattr(exc, 'message', None) or type(exc).__name__}")
        if self.fans is not None:  # fan chatter: low reliability, optional, never stops the analysis
            bundle = bundle.model_copy(update={"items": list(bundle.items), "errors": list(bundle.errors)})
            try:
                posts = await asyncio.wait_for(self.fans.fetch(ctx.match), timeout=30)
                bundle.items = [*bundle.items, *posts]
                if bundle.status in ("unavailable", "disabled") and posts:
                    bundle.status = "partial"
            except Exception as exc:  # noqa: BLE001
                bundle.errors.append(f"Fan posts skipped: {getattr(exc, 'message', None) or type(exc).__name__}")
        return bundle

    def _abstain(self, ctx: MatchContext, news: NewsBundle, reason: str, progress: Progress, warnings: list[str], **parts) -> AnalysisResult:
        for key, step in progress.steps.items():
            if step.status == "pending":
                progress.done(key, "Skipped: not enough reliable data")
        return AnalysisResult(
            match=ctx.match, abstained=True, abstain_reason=reason, data_quality=ctx.data_quality, sources=ctx.sources,
            news_items=news.items, context=ViewContext.of(ctx), data_availability=ctx.data_availability, warnings=warnings,
            prediction_record={"status": "not_saved_abstained"}, generated_at=now_iso(), **parts)

    def _store(self, ctx, sim, pred, quality_score, models) -> dict:
        if self.store is None:
            return {"status": "storage_disabled"}
        existing = self.store.get_prediction(ctx.match.id)
        if existing:
            return {"status": "already_exists", "created_at": existing["record"]["created_at"],
                    "note": "The original prediction for this match is kept unchanged; this analysis was not stored."}
        try:
            row = self.store.save_prediction(build_record(ctx, sim, pred, quality_score, models))
            return {"status": "saved", "created_at": row["record"]["created_at"], "digest": row["digest"]}
        except ImmutablePredictionError as exc:
            return {"status": "not_saved", "reason": exc.message}
        except AppError as exc:
            return {"status": "storage_error", "reason": exc.message}

    async def analyze(self, match_id: int, progress: Optional[Progress] = None) -> AnalysisResult:
        progress = progress or Progress()
        ctx = await self._step(progress, "data", self.provider.get_match_context(match_id))
        match = ctx.match
        warnings: list[str] = []

        progress.start("news")
        progress.start("scout")

        async def run_scout():
            if self.scout is None:
                return None
            try:
                return await asyncio.wait_for(self.scout.run(ctx.match), timeout=60)
            except Exception as exc:  # noqa: BLE001  scraping is optional
                log.warning("scout failed: %s", exc)
                return None

        news, scouted = await asyncio.gather(self._fetch_news(ctx), run_scout())
        if scouted is not None:
            web_err = next((e for e in news.errors if e.startswith("Web research skipped")), None)
            web_note = web_err or ("web research is disabled" if self.web is None else "web research found no reported absences")
            apply_scout(ctx, scouted, news.absences, web_note)
            progress.done("scout", "; ".join(f"{k}: {v}" for k, v in scouted.notes.items())[:300] + f"; {len(news.absences)} absences")
        else:
            progress.done("scout", "Scraping disabled" if self.scout is None else "Scraping failed: continued without it")
        progress.done("news", f"{sum(1 for i in news.items if i.origin == 'rss')} headlines" + (f" + {sum(1 for i in news.items if i.origin == 'web_research')} web findings" if any(i.origin == "web_research" for i in news.items) else "") if news.items else (news.errors[0] if news.errors else news.status))
        ctx.sources = [*ctx.sources, *[
            SourceRecord(id=i.item_id, name=i.source, url=i.link, retrieved_at=news.retrieved_at, published_at=i.published_at,
                         reliability=i.reliability_class, freshness=news_freshness(i.published_at)[0], note=f"news about {i.team_name}")
            for i in news.items]]
        ctx.data_quality = assess_quality(ctx, news)
        quality = ctx.data_quality
        if quality.abstain_recommended:
            return self._abstain(ctx, news, "Not enough reliable data to produce a prediction. " + "; ".join(
                i.detail for i in quality.issues if i.severity != "info")[:400], progress, warnings)

        progress.start("researcher")
        progress.start("intelligence")

        async def run_researcher():
            r = await self.researcher.run(ctx)
            progress.done("researcher")
            return r

        async def run_intelligence():
            r = await self.intelligence.run(match, news)
            progress.done("intelligence", "No usable news: skipped" if r.status == "unavailable" else None)
            return r

        try:
            research, intel = await asyncio.gather(run_researcher(), run_intelligence())
        except Exception as exc:  # noqa: BLE001
            for key in ("researcher", "intelligence"):
                if progress.steps[key].status == "running":
                    progress.fail(key, getattr(exc, "message", str(exc)))
            raise

        try:
            priors = build_priors(ctx)
        except InsufficientDataError as exc:
            return self._abstain(ctx, news, exc.message, progress, warnings, researcher=research, intelligence=intel)

        plan = await self._step(progress, "chief_plan", self.chief.plan(match, research, intel, quality, priors.to_dict()))
        if plan.abstain and plan.abstain_reason.strip():
            return self._abstain(ctx, news, f"Chief Predictor abstained: {plan.abstain_reason}", progress, warnings,
                                 researcher=research, intelligence=intel, chief_plan=plan)

        simulation = await self._step(progress, "simulation", asyncio.to_thread(
            self.simulate, priors, plan.adjustments, plan.scenarios, plan.openness_correlation, self.goal_cfg))
        prediction = await self._step(progress, "chief_final", self.chief.finalize(match, research, intel, plan, simulation, quality))

        warnings += [*normalize_prediction(prediction, simulation), *simulation.warnings, *priors.notes]
        cap_note = cap_confidence(prediction, quality.score)
        if cap_note:
            warnings.append(cap_note)
        models = {"statistical_researcher": self.researcher.client.model, "football_intelligence": self.intelligence.client.model,
                  "chief_predictor": self.chief.client.model}
        record = self._store(ctx, simulation, prediction, quality.score, models)

        return AnalysisResult(
            match=match, researcher=research, intelligence=intel, chief_plan=plan, simulation=simulation, prediction=prediction,
            data_quality=quality, sources=ctx.sources, news_items=news.items, context=ViewContext.of(ctx), data_availability=ctx.data_availability,
            warnings=warnings, models=models, prediction_record=record, generated_at=now_iso())
