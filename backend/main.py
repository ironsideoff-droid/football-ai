from __future__ import annotations

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
from agents.researcher import ResearcherAgent
from agents.tactical_analyst import TacticalAnalystAgent
from config import AGENT_NAMES, Settings, load_settings
from errors import AppError, ConfigError
from football_data.provider import FootballDataOrgProvider, FootballProvider
from services.groq_client import GroqClient
from services.jobs import JobStore
from services.orchestrator import Orchestrator


class AnalyzeRequest(BaseModel):
    match_id: int


def create_app(
    settings: Optional[Settings] = None,
    provider: Optional[FootballProvider] = None,
    orchestrator: Optional[Orchestrator] = None,
) -> FastAPI:
    settings = settings or load_settings()
    state: dict = {"provider": provider, "orchestrator": orchestrator, "clients": []}
    jobs = JobStore()

    def get_provider() -> FootballProvider:
        if state["provider"] is None:
            if not settings.football_api_key:
                raise ConfigError("FOOTBALL_API_KEY is missing. Add it to backend/.env.")
            state["provider"] = FootballDataOrgProvider(
                settings.football_api_key, settings.football_base_url, settings.request_timeout
            )
        return state["provider"]

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
                ResearcherAgent(clients[0]),
                TacticalAnalystAgent(clients[1]),
                ChiefPredictorAgent(clients[2]),
                settings.pass_research_to_tactical,
            )
        return state["orchestrator"]

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        for c in state["clients"]:
            await c.aclose()
        if state["provider"] is not None:
            await state["provider"].aclose()

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

    return app


app = create_app()
