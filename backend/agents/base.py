from __future__ import annotations

import json
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from errors import AgentOutputError
from services.groq_client import GroqClient

T = TypeVar("T", bound=BaseModel)


class BaseAgent:
    name = "Agent"
    system_prompt = ""

    def __init__(self, client: GroqClient):
        self.client = client

    async def _ask(self, payload: dict, schema: Type[T], system: str | None = None) -> T:
        user = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
        system = system or self.system_prompt
        problem = ""
        for attempt in range(2):  # one repair attempt if the JSON fails validation
            message = user if not problem else f"{user}\n\nYour previous answer was invalid: {problem}\nReturn the corrected JSON object only."
            raw = await self.client.chat_json(system, message)
            try:
                return schema.model_validate(raw)
            except ValidationError as exc:
                problem = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:6])
        raise AgentOutputError(f"{self.name} returned an invalid report ({problem}).", agent=self.name)
