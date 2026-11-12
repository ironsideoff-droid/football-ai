from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agents.chief_predictor import ChiefPredictorAgent
from agents.football_intelligence import FootballIntelligenceAgent
from agents.statistical_researcher import StatisticalResearcherAgent
from auth import DEV_USER, AuthUser, Authenticator, admin_secret_ok, bearer_token
from billing.store import BillingStore, MemoryBillingStore, PostgresBillingStore, is_active
from billing.stripe_service import BillingService
from config import AGENT_NAMES, Settings, load_settings
from errors import (AnalysisInProgressError, AppError, BillingError, ConfigError, ForbiddenError, SubscriptionRequiredError, UsageLimitError)
from evaluation import backtesting, metrics
from evaluation.store import PredictionStore
from football_data.news import DisabledNews, GoogleNewsRssProvider, NewsProvider
from football_data.provider import FootballDataOrgProvider, FootballProvider
from services.groq_client import GroqClient
from services.jobs import JobStore
from services.orchestrator import Orchestrator
from services.results import refresh_results
from simulation.goal_model import GoalModelConfig

if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("football_ai")

EVALUATION_CACHE_SECONDS = 60
MAX_WEBHOOK_BYTES = 1_000_000


class AnalyzeRequest(BaseModel):
    match_id: int = Field(gt=0, lt=10**10)


def create_app(
    settings: Optional[Settings] = None,
    provider: Optional[FootballProvider] = None,
    orchestrator: Optional[Orchestrator] = None,
    store: Optional[PredictionStore] = None,
    billing: Optional[BillingStore] = None,
) -> FastAPI:
    settings = settings or load_settings()
    state: dict = {"provider": provider, "orchestrator": orchestrator, "clients": [], "news": None, "pool": None}

    # ---- storage: Postgres in production (DATABASE_URL), local files / memory for development and tests
    if settings.database_url and (store is None or billing is None):
        from db import create_pool
        from evaluation.pg_store import PostgresPredictionStore
        state["pool"] = create_pool(settings.database_url)
        store = store or PostgresPredictionStore(state["pool"])
        billing = billing or PostgresBillingStore(state["pool"])
    store = store or PredictionStore(settings.predictions_dir)
    if billing is None and not settings.auth_enabled:
        billing = MemoryBillingStore()  # nobody is billed when auth is off (local development)

    jobs = JobStore(max_concurrent=settings.max_concurrent_analyses)
    authn = Authenticator(settings)
    eval_cache: dict = {"at": 0.0, "body": None}

    def require_billing() -> BillingStore:
        if billing is None:
            raise ConfigError("DATABASE_URL is not configured on the server.")
        return billing

    # ---- dependencies
    def current_user(authorization: Optional[str] = Header(None)) -> AuthUser:
        if not settings.auth_enabled:
            return DEV_USER
        return authn.verify(bearer_token(authorization))

    def subscriber(user: AuthUser = Depends(current_user)) -> AuthUser:
        if settings.auth_enabled and not is_active(require_billing().get_subscription(user.id)):
            raise SubscriptionRequiredError("An active subscription is required to run analyses.")
        return user

    def admin(x_admin_secret: Optional[str] = Header(None)) -> None:
        if not settings.auth_enabled:
            return
        if not settings.admin_secret:
            raise ConfigError("ADMIN_SECRET is not configured on the server.")
        if not admin_secret_ok(settings.admin_secret, x_admin_secret):
            raise ForbiddenError("Forbidden.")

    # ---- lazily built external clients
    def get_provider() -> FootballProvider:
        if state["provider"] is None:
            if not settings.football_api_key:
                raise ConfigError("FOOTBALL_API_KEY is missing. Add it to backend/.env.")
            state["provider"] = FootballDataOrgProvider(
                settings.football_api_key, settings.football_base_url, settings.request_timeout
            )
        return state["provider"]

    def get_news() -> NewsProvider:
        if state["news"] is None:
            state["news"] = GoogleNewsRssProvider(timeout=15.0) if settings.news_enabled else DisabledNews()
        return state["news"]

    def get_orchestrator() -> Orchestrator:
        if state["orchestrator"] is None:
            missing = settings.missing_groq()
            if missing:
                raise ConfigError(f"Missing Groq credentials: {', '.join(missing)}. Add them to backend/.env.")
            clients = [
                GroqClient(c.name, c.api_key, c.model, settings.groq_base_url, settings.request_timeout)
                for c in (settings.agent(i) for i in range(3))
            ]
            state["clients"] = clients
            state["orchestrator"] = Orchestrator(
                get_provider(),
                StatisticalResearcherAgent(clients[0]),
                FootballIntelligenceAgent(clients[1]),
                ChiefPredictorAgent(clients[2]),
                news=get_news(),
                store=store,
                goal_cfg=GoalModelConfig(model=settings.goal_model, dc_rho=settings.dc_rho, bp_share=settings.bp_share),  # type: ignore[arg-type]
            )
        return state["orchestrator"]

    def billing_service() -> BillingService:
        return BillingService(settings, require_billing())

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        for problem in settings.production_problems():
            log.error("Configuration problem: %s", problem)
        yield
        for c in state["clients"]:
            await c.aclose()
        if state["provider"] is not None:
            await state["provider"].aclose()
        if state["news"] is not None:
            await state["news"].aclose()
        if state["pool"] is not None:
            state["pool"].close()

    app = FastAPI(title="Football AI", lifespan=lifespan, docs_url=None if settings.auth_enabled else "/docs",
                  redoc_url=None, openapi_url=None if settings.auth_enabled else "/openapi.json")
    app.add_middleware(
        CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"], max_age=600,
    )

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"error": {"code": "internal_error", "message": "Something went wrong. Please try again.", "agent": None}})

    # ================================================================== public
    @app.get("/api/health")
    async def health():
        if settings.production_problems():
            return JSONResponse(status_code=503, content={"status": "misconfigured"})  # fails the deploy's health check
        if settings.auth_enabled:
            return {"status": "ok"}  # nothing about configuration is public in production
        warning = settings.duplicate_key_warning()
        return {
            "status": "ok",
            "football_api_configured": bool(settings.football_api_key),
            "agents": [
                {"name": AGENT_NAMES[i], "key_configured": bool(settings.groq_keys[i]), "model": settings.groq_models[i]}
                for i in range(3)
            ],
            "warnings": [warning] if warning else [],
            "news_enabled": settings.news_enabled,
            "goal_model": settings.goal_model,
        }

    def variant(rows: list[dict], prefix: str) -> list[dict]:
        key = lambda k: f"{prefix}{k}"  # noqa: E731
        return [{**r, "probs": r["sim_probs"] if prefix else r["probs"], "p_btts": r.get(key("p_btts")), "p_over_2_5": r.get(key("p_over_2_5"))}
                for r in rows]

    def compute_evaluation() -> dict:
        rows = store.joined()
        preds = store.predictions()
        freq = None
        if rows:
            outs = [metrics.outcome_of(r["actual"]["home"], r["actual"]["away"]) for r in rows]
            freq = {k: max(outs.count(k) / len(outs), 1e-6) for k in ("home", "draw", "away")}
        durable = state["pool"] is not None or hasattr(store, "pool")
        return {
            "stored_predictions": len(preds), "evaluated": len(rows), "tampered_match_ids": store.verify(),
            "chief_predictor": metrics.evaluate(rows), "simulation_only": metrics.evaluate(variant(rows, "sim_")),
            "baseline_constant_frequencies": metrics.evaluate(metrics.baseline_records(rows, freq)) if freq else None,
            "storage_note": ("Every prediction is stored once, before kickoff, in a database that blocks edits and deletions."
                             if durable else
                             "Predictions are stored once, in append-only files on this server's disk. On hosts with ephemeral disks they are lost on redeploy."),
        }

    @app.get("/api/evaluation")  # public on purpose: the track record is the product's proof
    async def evaluation():
        if eval_cache["body"] is None or time.monotonic() - eval_cache["at"] > EVALUATION_CACHE_SECONDS:
            eval_cache["body"], eval_cache["at"] = await asyncio.to_thread(compute_evaluation), time.monotonic()
        return eval_cache["body"]

    # ================================================================== signed-in users
    @app.get("/api/fixtures")
    async def fixtures(days: int = Query(14, ge=1, le=30), competitions: Optional[str] = Query(None, max_length=60),
                       _: AuthUser = Depends(current_user)):
        today = datetime.now(timezone.utc).date()
        matches = await get_provider().list_fixtures(
            today.isoformat(), (today + timedelta(days=days)).isoformat(), competitions
        )
        return {"matches": [m.model_dump() for m in matches]}

    @app.get("/api/billing/status")
    async def billing_status(user: AuthUser = Depends(current_user)):
        limit = settings.daily_analysis_limit
        if not settings.auth_enabled:
            return {"active": True, "status": "dev", "current_period_end": None, "cancel_at_period_end": False,
                    "daily_limit": limit, "used_today": 0, "email": user.email}
        b = require_billing()
        today = datetime.now(timezone.utc).date()
        sub, used = await asyncio.gather(asyncio.to_thread(b.get_subscription, user.id), asyncio.to_thread(b.used, user.id, today))
        return {"active": is_active(sub), "status": (sub or {}).get("status", "none"), "current_period_end": (sub or {}).get("current_period_end"),
                "cancel_at_period_end": bool((sub or {}).get("cancel_at_period_end")), "daily_limit": limit, "used_today": used, "email": user.email}

    @app.post("/api/billing/checkout")
    async def billing_checkout(user: AuthUser = Depends(current_user)):
        return {"url": await asyncio.to_thread(billing_service().create_checkout, user)}

    @app.post("/api/billing/portal")
    async def billing_portal(user: AuthUser = Depends(current_user)):
        return {"url": await asyncio.to_thread(billing_service().create_portal, user)}

    @app.get("/api/jobs/{job_id}")
    async def job_status(job_id: str, user: AuthUser = Depends(current_user)):
        job = jobs.get(job_id, user.id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job id.")
        return job.to_dict()

    # ================================================================== subscribers
    async def release(user_id: str, day) -> None:
        await asyncio.to_thread(require_billing().release_use, user_id, day)

    async def spend_allowance(user: AuthUser) -> "datetime.date":
        day = datetime.now(timezone.utc).date()
        allowed, used = await asyncio.to_thread(require_billing().try_use, user.id, day, settings.daily_analysis_limit)
        if not allowed:
            raise UsageLimitError(f"Daily limit of {settings.daily_analysis_limit} new analyses reached ({used} used). It resets at 00:00 UTC. "
                                  "Matches that were already analysed are always available.")
        return day

    @app.get("/api/matches/{match_id}")
    async def match_detail(match_id: int, _: AuthUser = Depends(subscriber)):
        return (await get_provider().get_match_context(match_id)).model_dump()

    @app.post("/api/analyze")
    async def analyze(body: AnalyzeRequest, user: AuthUser = Depends(subscriber)):
        cached = await asyncio.to_thread(store.get_analysis, body.match_id)
        if cached is not None:
            return cached
        orch = get_orchestrator()
        day = await spend_allowance(user)
        try:
            async with jobs.limiter:
                result = await orch.analyze(body.match_id)
        except Exception:
            await release(user.id, day)
            raise
        return result.model_dump()

    @app.post("/api/analyze/start", status_code=202)
    async def analyze_start(body: AnalyzeRequest, user: AuthUser = Depends(subscriber)):
        running = jobs.running_for(user.id)
        if running is not None:
            if running.match_id == body.match_id:
                return {"job_id": running.id}
            raise AnalysisInProgressError("Another analysis is still running for your account. Wait for it to finish.")
        cached = await asyncio.to_thread(store.get_analysis, body.match_id)
        if cached is not None:  # this match was already analysed: serve that analysis, no LLM calls, no allowance used
            return {"job_id": jobs.start_cached(body.match_id, user.id, cached).id}
        orch = get_orchestrator()
        day = await spend_allowance(user)
        job = jobs.start(orch, body.match_id, user.id, on_failure=lambda: release(user.id, day))
        return {"job_id": job.id}

    @app.get("/api/predictions")
    async def predictions(_: AuthUser = Depends(subscriber)):
        rows = await asyncio.to_thread(store.predictions)
        return {"predictions": [{k: v for k, v in p["record"].items() if k != "score_matrix"} for p in rows]}

    # ================================================================== Stripe
    @app.post("/api/stripe/webhook")
    async def stripe_webhook(request: Request, stripe_signature: Optional[str] = Header(None)):
        payload = await request.body()
        if len(payload) > MAX_WEBHOOK_BYTES:
            raise BillingError("Payload too large.")
        outcome = await asyncio.to_thread(billing_service().handle_webhook, payload, stripe_signature)
        return {"received": True, "outcome": outcome}

    # ================================================================== operator (X-Admin-Secret)
    @app.post("/api/evaluation/refresh")
    async def evaluation_refresh(_: None = Depends(admin)):
        recorded = await refresh_results(store, get_provider())
        if recorded:
            eval_cache["body"] = None
        return {"results_recorded": recorded}

    @app.get("/api/backtest")
    async def backtest(competition: str = Query("PL", min_length=2, max_length=8), _: None = Depends(admin)):
        matches = await get_provider().get_competition_results(competition.upper())
        report = await asyncio.to_thread(backtesting.walk_forward, matches)
        report["competition"] = competition.upper()
        return report

    return app


app = create_app()
