from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MODEL = "llama-3.3-70b-versatile"
AGENT_NAMES = ("Statistical Researcher", "Football Intelligence", "Chief Predictor")


@dataclass(frozen=True)
class AgentConfig:
    name: str
    api_key: str
    model: str


@dataclass(frozen=True)
class Settings:
    groq_keys: tuple[str, str, str]
    groq_models: tuple[str, str, str]
    football_api_key: str
    football_base_url: str = "https://api.football-data.org/v4"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")
    request_timeout: float = 60.0
    news_enabled: bool = True
    goal_model: str = "poisson"
    dc_rho: float = -0.10
    bp_share: float = 0.10
    predictions_dir: str = "data_store"
    # accounts & billing
    auth_disabled: bool = False  # local development only; production fails closed
    supabase_url: str = ""
    supabase_anon_key: str = ""  # public (publishable) key
    supabase_service_key: str = ""  # secret: server-side writes of subscription state
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_id: str = ""  # optional; otherwise a 9.99 EUR/month price is created inline
    app_url: str = ""
    scout_enabled: bool = True  # Agent 4 scraping (ESPN, Understat); each source fails independently
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    web_research: bool = False
    web_research_model: str = "openai/gpt-oss-20b"
    web_research_key: str = ""
    admin_emails: tuple[str, ...] = ()  # these verified accounts use the app without paying
    price_cents: int = 999
    price_currency: str = "eur"

    def agent(self, index: int) -> AgentConfig:
        return AgentConfig(AGENT_NAMES[index], self.groq_keys[index], self.groq_models[index])

    def missing_groq(self) -> list[str]:
        return [f"GROQ_API_KEY_{i + 1}" for i, k in enumerate(self.groq_keys) if not k]

    def duplicate_key_warning(self) -> str | None:
        keys = [k for k in self.groq_keys if k]
        if len(set(keys)) < len(keys):
            return "Two or more GROQ_API_KEY_* values are identical; use three different Groq accounts."
        return None


def load_settings() -> Settings:
    load_dotenv(Path(__file__).resolve().parent / ".env")
    env = os.environ.get
    keys = tuple((env(f"GROQ_API_KEY_{i}") or "").strip() for i in (1, 2, 3))
    models = tuple((env(f"GROQ_MODEL_{i}") or "").strip() or DEFAULT_MODEL for i in (1, 2, 3))
    return Settings(
        groq_keys=keys,  # type: ignore[arg-type]
        groq_models=models,  # type: ignore[arg-type]
        football_api_key=(env("FOOTBALL_API_KEY") or "").strip(),
        news_enabled=(env("NEWS_ENABLED", "true").lower() != "false"),
        goal_model=(env("GOAL_MODEL") or "poisson").strip().lower() if (env("GOAL_MODEL") or "poisson").strip().lower() in ("poisson", "dixon_coles", "bivariate") else "poisson",
        dc_rho=float(env("DC_RHO") or -0.10),
        bp_share=float(env("BP_SHARE") or 0.10),
        auth_disabled=(env("AUTH_DISABLED", "false").lower() == "true"),
        supabase_url=(env("SUPABASE_URL") or "").strip().rstrip("/"),
        supabase_anon_key=(env("SUPABASE_ANON_KEY") or "").strip(),
        supabase_service_key=(env("SUPABASE_SERVICE_KEY") or "").strip(),
        stripe_secret_key=(env("STRIPE_SECRET_KEY") or "").strip(),
        stripe_webhook_secret=(env("STRIPE_WEBHOOK_SECRET") or "").strip(),
        stripe_price_id=(env("STRIPE_PRICE_ID") or "").strip(),
        admin_emails=tuple(e.strip().lower() for e in (env("ADMIN_EMAILS") or "").split(",") if e.strip()),
        scout_enabled=(env("SCOUT_ENABLED", "true").lower() != "false"),
        reddit_client_id=(env("REDDIT_CLIENT_ID") or "").strip(),
        reddit_client_secret=(env("REDDIT_CLIENT_SECRET") or "").strip(),
        web_research=(env("WEB_RESEARCH", "false").lower() == "true"),
        web_research_model=(env("WEB_RESEARCH_MODEL") or "openai/gpt-oss-20b").strip(),
        web_research_key=(env("GROQ_API_KEY_4") or "").strip(),
        app_url=(env("APP_URL") or env("RENDER_EXTERNAL_URL") or "").strip().rstrip("/"),
        predictions_dir=(env("PREDICTIONS_DIR") or str(Path(__file__).resolve().parent / "data_store")),
    )
