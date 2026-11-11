from __future__ import annotations

import json
from typing import Any, Optional, Type, TypeVar

import numpy as np
from pydantic import BaseModel, ValidationError

from errors import AgentOutputError
from services.groq_client import GroqClient

T = TypeVar("T", bound=BaseModel)


def _empty(v: Any) -> bool:
    return v is None or (isinstance(v, (str, list, tuple, dict)) and len(v) == 0)


def compact(obj: Any) -> Any:
    """Drop empty values and round floats so prompts stay small (tokens-per-minute limits are tight on free tiers)."""
    if isinstance(obj, np.ndarray):
        obj = obj.tolist()
    elif isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, dict):
        out = {k: compact(v) for k, v in obj.items()}
        return {k: v for k, v in out.items() if not _empty(v)}
    if isinstance(obj, (list, tuple)):
        return [compact(v) for v in obj]
    if isinstance(obj, float):
        return round(obj, 3)
    return obj


class BaseAgent:
    name = "Agent"
    system_prompt = ""

    def __init__(self, client: GroqClient):
        self.client = client

    async def _ask(self, payload: dict, schema: Type[T], system: str | None = None, max_tokens: Optional[int] = None,
                   compact_payload: bool = False) -> T:
        user = json.dumps(compact(payload) if compact_payload else payload, ensure_ascii=False, separators=(",", ":"), default=str)
        system = system or self.system_prompt
        problem = ""
        for attempt in range(2):  # one repair attempt if the JSON fails validation
            message = user if not problem else f"{user}\n\nYour previous answer was invalid: {problem}\nReturn the corrected JSON object only."
            raw = await self.client.chat_json(system, message, **({"max_tokens": max_tokens} if max_tokens else {}))
            try:
                return schema.model_validate(raw)
            except ValidationError as exc:
                problem = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:6])
        raise AgentOutputError(f"{self.name} returned an invalid report ({problem}).", agent=self.name)
