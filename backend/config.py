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
    # --- production (all optional for local development, where auth_enabled is False) ---
    auth_enabled: bool = False
    database_url: str = ""
    supabase_url: str = ""
    supabase_jwt_secret: str = ""  # only for projects that still use the legacy HS256 secret; new projects use JWKS
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_id: str = ""
    stripe_automatic_tax: bool = False
    app_url: str = "http://localhost:5173"  # public URL of the frontend, used for Stripe return links
    admin_secret: str = ""  # protects /api/admin/* (X-Admin-Secret header)
    daily_analysis_limit: int = 30
    max_concurrent_analyses: int = 3

    def agent(self, index: int) -> AgentConfig:
        return AgentConfig(AGENT_NAMES[index], self.groq_keys[index], self.groq_models[index])

    def missing_groq(self) -> list[str]:
        return [f"GROQ_API_KEY_{i + 1}" for i, k in enumerate(self.groq_keys) if not k]

    def duplicate_key_warning(self) -> str | None:
        keys = [k for k in self.groq_keys if k]
        if len(set(keys)) < len(keys):
            return "Two or more GROQ_API_KEY_* values are identical; use three different Groq accounts."
        return None


    def production_problems(self) -> list[str]:
        """Configuration that is missing for a real deployment. Empty when everything needed is set."""
        if not self.auth_enabled:
            return []
        need = {
            "DATABASE_URL": self.database_url, "SUPABASE_URL": self.supabase_url, "STRIPE_SECRET_KEY": self.stripe_secret_key,
            "STRIPE_WEBHOOK_SECRET": self.stripe_webhook_secret, "STRIPE_PRICE_ID": self.stripe_price_id,
            "ADMIN_SECRET": self.admin_secret, "FOOTBALL_API_KEY": self.football_api_key,
        }
        problems = [f"{k} is not set" for k, v in need.items() if not v]
        problems += [f"{k} is not set" for k in self.missing_groq()]
        if self.app_url.startswith("http://localhost"):
            problems.append("APP_URL still points at localhost")
        return problems


def _flag(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value and value.strip() else default
    except ValueError:
        return default


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
        predictions_dir=(env("PREDICTIONS_DIR") or str(Path(__file__).resolve().parent / "data_store")),
        # Secure by default: the API requires a login and a subscription unless AUTH_ENABLED=false is set explicitly.
        auth_enabled=_flag(env("AUTH_ENABLED"), True),
        database_url=(env("DATABASE_URL") or "").strip(),
        supabase_url=(env("SUPABASE_URL") or "").strip().rstrip("/"),
        supabase_jwt_secret=(env("SUPABASE_JWT_SECRET") or "").strip(),
        stripe_secret_key=(env("STRIPE_SECRET_KEY") or "").strip(),
        stripe_webhook_secret=(env("STRIPE_WEBHOOK_SECRET") or "").strip(),
        stripe_price_id=(env("STRIPE_PRICE_ID") or "").strip(),
        stripe_automatic_tax=_flag(env("STRIPE_AUTOMATIC_TAX"), False),
        app_url=(env("APP_URL") or "http://localhost:5173").strip().rstrip("/"),
        admin_secret=(env("ADMIN_SECRET") or "").strip(),
        daily_analysis_limit=_int(env("DAILY_ANALYSIS_LIMIT"), 30),
        max_concurrent_analyses=max(1, _int(env("MAX_CONCURRENT_ANALYSES"), 3)),
        **({"cors_origins": tuple(o.strip() for o in env("CORS_ORIGINS", "").split(",") if o.strip())} if (env("CORS_ORIGINS") or "").strip() else {}),
    )
