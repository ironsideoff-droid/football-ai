from __future__ import annotations


class AppError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, agent: str | None = None):
        super().__init__(message)
        self.message = message
        self.agent = agent

    def to_dict(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "agent": self.agent}}


class ConfigError(AppError):
    status_code, code = 503, "configuration_error"


class GroqAuthError(AppError):
    status_code, code = 502, "groq_auth_error"


class GroqRateLimitError(AppError):
    status_code, code = 429, "groq_rate_limit"


class GroqServiceError(AppError):
    status_code, code = 502, "groq_service_error"


class AgentOutputError(AppError):
    status_code, code = 502, "invalid_agent_output"


class FootballAuthError(AppError):
    status_code, code = 502, "football_auth_error"


class FootballRateLimitError(AppError):
    status_code, code = 429, "football_rate_limit"


class FootballNotFoundError(AppError):
    status_code, code = 404, "football_not_found"


class FootballDataError(AppError):
    status_code, code = 502, "football_data_error"


class SimulationError(AppError):
    status_code, code = 500, "simulation_error"
