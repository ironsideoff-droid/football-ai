from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MODEL = "llama-3.3-70b-versatile"
AGENT_NAMES = ("Researcher", "Tactical Analyst", "Chief Predictor")


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
    pass_research_to_tactical: bool = False

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
        pass_research_to_tactical=(env("PASS_RESEARCH_TO_TACTICAL", "false").lower() == "true"),
    )
