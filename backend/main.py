from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel

from agents.chief_predictor import ChiefPredictorAgent
from agents.football_intelligence import FootballIntelligenceAgent
from agents.statistical_researcher import StatisticalResearcherAgent
from config import AGENT_NAMES, Settings, load_settings
from errors import AppError, ConfigError
from football_data.provider import FootballDataOrgProvider, FootballProvider
from evaluation import backtesting, metrics
from evaluation.store import PredictionStore
from football_data.news import DisabledNews, GoogleNewsRssProvider, NewsProvider
from services.groq_client import GroqClient
from simulation.goal_model import GoalModelConfig
from services.jobs import JobStore
from services.orchestrator import Orchestrator


class AnalyzeRequest(BaseModel):
    match_id: int


def create_app(
    settings: Optional[Settings] = None,
    provider: Optional[FootballProvider] = None,
    orchestrator: Optional[Orchestrator] = None,
    store: Optional[PredictionStore] = None,
) -> FastAPI:
    settings = settings or load_settings()
    store = store or PredictionStore(settings.predictions_dir)
    state: dict = {"provider": provider, "orchestrator": orchestrator, "clients": [], "news": None}
    jobs = JobStore()

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

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        for c in state["clients"]:
            await c.aclose()
        if state["provider"] is not None:
            await state["provider"].aclose()
        if state["news"] is not None:
            await state["news"].aclose()

    app = FastAPI(title="Football AI", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["*"], allow_headers=["*"]
    )

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.get("/api/health")
    async def health():
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

    @app.get("/api/fixtures")
    async def fixtures(days: int = Query(7, ge=1, le=10), competitions: Optional[str] = None):
        today = datetime.now(timezone.utc).date()
        matches = await get_provider().list_fixtures(
            today.isoformat(), (today + timedelta(days=days)).isoformat(), competitions
        )
        return {"matches": [m.model_dump() for m in matches]}

    @app.get("/api/matches/{match_id}")
    async def match_detail(match_id: int):
        return (await get_provider().get_match_context(match_id)).model_dump()

    @app.post("/api/analyze")
    async def analyze(body: AnalyzeRequest):
        result = await get_orchestrator().analyze(body.match_id)
        return result.model_dump()

    @app.post("/api/analyze/start", status_code=202)
    async def analyze_start(body: AnalyzeRequest):
        job = jobs.start(get_orchestrator(), body.match_id)
        return {"job_id": job.id}

    @app.get("/api/jobs/{job_id}")
    async def job_status(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job id.")
        return job.to_dict()

    static = Path(__file__).resolve().parent / "static"
    if static.is_dir():  # production: serve the built React app from the same service
        app.mount("/", StaticFiles(directory=static, html=True), name="web")

    def variant(rows: list[dict], prefix: str) -> list[dict]:
        key = lambda k: f"{prefix}{k}"  # noqa: E731
        return [{**r, "probs": r["sim_probs"] if prefix else r["probs"], "p_btts": r.get(key("p_btts")), "p_over_2_5": r.get(key("p_over_2_5"))}
                for r in rows]

    @app.get("/api/evaluation")
    async def evaluation():
        rows = store.joined()
        preds = store.predictions()
        freq = None
        if rows:
            outs = [metrics.outcome_of(r["actual"]["home"], r["actual"]["away"]) for r in rows]
            freq = {k: max(outs.count(k) / len(outs), 1e-6) for k in ("home", "draw", "away")}
        return {
            "stored_predictions": len(preds), "evaluated": len(rows), "tampered_match_ids": store.verify(),
            "chief_predictor": metrics.evaluate(rows), "simulation_only": metrics.evaluate(variant(rows, "sim_")),
            "baseline_constant_frequencies": metrics.evaluate(metrics.baseline_records(rows, freq)) if freq else None,
            "storage_note": "Predictions are stored once, in append-only files on this server's disk. On hosts with ephemeral disks (e.g. Render free plan) they are lost on redeploy.",
        }

    @app.post("/api/evaluation/refresh")
    async def evaluation_refresh():
        recorded = []
        for p in store.predictions():
            rec = p["record"]
            if store.get_result(rec["match_id"]):
                continue
            if datetime.fromisoformat(rec["kickoff_utc"].replace("Z", "+00:00")) > datetime.now(timezone.utc):
                continue
            try:
                ctx = await get_provider().get_match_context(rec["match_id"])
            except AppError:
                continue
            m = ctx.match
            if m.status == "FINISHED" and m.score_home is not None and m.score_away is not None:
                if store.save_result(m.id, m.score_home, m.score_away):
                    recorded.append(m.id)
        return {"results_recorded": recorded}

    @app.get("/api/predictions")
    async def predictions():
        return {"predictions": [{k: v for k, v in p["record"].items() if k != "score_matrix"} for p in store.predictions()]}

    @app.get("/api/backtest")
    async def backtest(competition: str = Query("PL", min_length=2, max_length=8)):
        matches = await get_provider().get_competition_results(competition.upper())
        report = await asyncio.to_thread(backtesting.walk_forward, matches)
        report["competition"] = competition.upper()
        return report

    return app


app = create_app()
