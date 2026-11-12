from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Optional

import httpx

from errors import AgentOutputError, GroqAuthError, GroqRateLimitError, GroqServiceError

log = logging.getLogger("football_ai.groq")


MIN_OUTPUT_TOKENS = 900  # below this a JSON report cannot be completed
AGENT_INDEX = {"Statistical Researcher": 1, "Football Intelligence": 2, "Chief Predictor": 3}


def extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


class GroqClient:
    """One client per agent: separate API key, model and HTTP session."""

    def __init__(
        self,
        name: str,
        api_key: str,
        model: str,
        base_url: str = "https://api.groq.com/openai/v1",
        timeout: float = 60.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        sleep=asyncio.sleep,
        max_retries: int = 2,
    ):
        self.name, self.model = name, model
        self._sleep, self._max_retries = sleep, max_retries
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
        )

    @staticmethod
    def _too_large(resp: httpx.Response) -> Optional[tuple[int, int]]:
        """(limit, requested) tokens when Groq says the request exceeds the tokens-per-minute limit of the model."""
        try:
            text = resp.json()["error"]["message"]
        except Exception:  # noqa: BLE001
            text = resp.text
        found = re.search(r"Limit (\d+), Requested (\d+)", text)
        return (int(found.group(1)), int(found.group(2))) if found and "too large" in text.lower() else None

    async def aclose(self) -> None:
        await self._http.aclose()

    async def chat_json(self, system: str, user: str, *, temperature: float = 0.2, max_tokens: int = 3500) -> dict:
        # Logged: agent, model, timing, status and token counts. Never the prompts or the model's output.
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        attempt = 0
        adapted = False
        t0 = time.monotonic()
        while True:
            try:
                resp = await self._http.post("/chat/completions", json=payload)
            except httpx.TimeoutException as exc:
                log.warning("groq timeout", extra={"agent": self.name, "model": self.model, "attempt": attempt + 1,
                                                   "duration_ms": round((time.monotonic() - t0) * 1000)})
                raise GroqServiceError("Groq request timed out.", agent=self.name) from exc
            except httpx.HTTPError as exc:
                log.warning("groq unreachable", extra={"agent": self.name, "model": self.model, "error_type": type(exc).__name__})
                raise GroqServiceError(f"Could not reach Groq: {exc}", agent=self.name) from exc

            code = resp.status_code
            if code >= 400 and not adapted:
                too_large = self._too_large(resp)
                if too_large:
                    limit, requested = too_large
                    prompt_tokens = requested - payload["max_tokens"]
                    room = limit - prompt_tokens - 100
                    if room >= MIN_OUTPUT_TOKENS:
                        log.warning("groq request too large; shrinking reserved output and retrying",
                                    extra={"agent": self.name, "model": self.model, "limit": limit, "requested": requested, "new_max_tokens": room})
                        payload["max_tokens"], adapted = room, True  # shrink only the reserved output and retry once
                        continue
                    raise GroqServiceError(
                        f"The request for {self.name} needs about {prompt_tokens} input tokens, but the model's per-minute limit is {limit}. "
                        f"Choose a model with a higher limit in GROQ_MODEL_{AGENT_INDEX.get(self.name, 3)}.", agent=self.name)
                if code == 413:
                    raise GroqServiceError(f"The request for {self.name} was too large for the model.", agent=self.name)
            if code in (401, 403):
                log.error("groq rejected the API key", extra={"agent": self.name, "model": self.model, "status": code})
                raise GroqAuthError(f"The Groq API key for {self.name} was rejected.", agent=self.name)
            if code == 429 or code >= 500:
                if attempt < self._max_retries:
                    wait = float(resp.headers.get("retry-after", 2 ** (attempt + 1)))
                    log.warning("groq retry", extra={"agent": self.name, "model": self.model, "status": code, "attempt": attempt + 1, "wait_s": min(wait, 20)})
                    await self._sleep(min(wait, 20))
                    attempt += 1
                    continue
                log.warning("groq gave up", extra={"agent": self.name, "model": self.model, "status": code, "attempts": attempt + 1})
                if code == 429:
                    raise GroqRateLimitError(
                        f"Groq rate limit reached for {self.name}. Wait a minute or use a model with higher limits.",
                        agent=self.name,
                    )
                raise GroqServiceError(f"Groq returned HTTP {code}.", agent=self.name)
            if code >= 400:
                try:
                    detail = resp.json()["error"]["message"]
                except Exception:  # noqa: BLE001
                    detail = resp.text[:200]
                raise GroqServiceError(f"Groq rejected the request for {self.name}: {detail}", agent=self.name)
            break

        try:
            body = resp.json()
            usage = body.get("usage") or {}
            log.info("groq call", extra={"agent": self.name, "model": self.model, "attempts": attempt + 1, "duration_ms": round((time.monotonic() - t0) * 1000),
                                         "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens")})
            content = body["choices"][0]["message"]["content"]
            return extract_json(content)
        except (KeyError, IndexError, ValueError) as exc:
            log.warning("groq returned invalid JSON", extra={"agent": self.name, "model": self.model})
            raise AgentOutputError(f"{self.name} did not return valid JSON.", agent=self.name) from exc
