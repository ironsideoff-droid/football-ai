"""Optional "deep research" step: a Groq model with the built-in `browser_search` tool looks for recent injury, suspension,
lineup and press-conference news and returns short, sourced claims.

Safeguards (the model is never trusted on its own word):
* every claim must carry an http(s) URL; claims without one are dropped;
* when the response shows what the search tool actually returned, a claim whose URL is not in that evidence is dropped;
* claims are labelled origin="web_research" so the agents treat them as lower-reliability summaries, never as quotes;
* any failure (rate limit, timeout, unexpected answer) only skips this step - the analysis continues without it.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx

from errors import AppError
from football_data.source_manager import classify_source, news_freshness
from schemas.match import MatchSummary
from schemas.news import NewsItem

MAX_PER_TEAM = 5
SYSTEM = """You are a football news researcher with a web search tool. Search the web for the most recent (last 7 days) news that
could affect the match below: injuries, suspensions, doubtful players, expected or confirmed lineups, and statements by the
managers. Search for BOTH teams. Use only information you actually read on a page.
Answer with ONLY this JSON (no other text after your searching):
{"findings": [{"team": "home" | "away", "claim": "ONE factual sentence, at most 180 characters, exactly supported by the page",
 "url": "the full https URL of the page you read", "source": "publisher name", "published": "YYYY-MM-DD or null"}]}
At most 5 findings per team. If you find nothing reliable return {"findings": []}. Never invent a claim, a URL, a player or a date."""


class WebResearchError(AppError):
    status_code, code = 502, "web_research_error"


def _json_tail(text: str) -> tuple[str, dict]:
    """Splits the answer into (evidence_before_json, parsed_json)."""
    start = text.rfind('{"findings"')
    if start < 0:
        start = text.find("{")
    if start < 0:
        raise ValueError("no JSON in the answer")
    end = text.rfind("}")
    return text[:start], json.loads(text[start:end + 1])


def parse_findings(message: dict, match: MatchSummary, now: Optional[datetime] = None) -> list[NewsItem]:
    now = now or datetime.now(timezone.utc)
    content = message.get("content") or ""
    try:
        before, data = _json_tail(content)
    except (ValueError, json.JSONDecodeError) as exc:
        raise WebResearchError("The web research answer was not valid JSON.") from exc
    # whatever the response shows besides the final JSON counts as evidence of what the search tool returned
    evidence = before + json.dumps({k: v for k, v in message.items() if k != "content"}, ensure_ascii=False, default=str)
    items: list[NewsItem] = []
    seen: set[str] = set()
    per_team = {"home": 0, "away": 0}
    for f in data.get("findings") or []:
        if not isinstance(f, dict):
            continue
        side, claim, url = f.get("team"), str(f.get("claim") or "").strip(), str(f.get("url") or "").strip()
        if side not in per_team or not claim or len(claim) > 240 or per_team[side] >= MAX_PER_TEAM:
            continue
        if not re.match(r"^https?://[^\s]+$", url) or len(url) > 400:
            continue
        if len(evidence.strip()) > 20 and url not in evidence:
            continue  # the model named a page its own search results do not contain
        key = re.sub(r"\W+", " ", claim.lower())
        if key in seen:
            continue
        seen.add(key)
        published = None
        try:
            if f.get("published"):
                published = datetime.fromisoformat(str(f["published"])[:10]).replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
        except ValueError:
            pass
        _, age = news_freshness(published, now)
        host = urlparse(url).netloc
        source = str(f.get("source") or host or "web").strip()[:80]
        digest = hashlib.sha1(f"{side}|{claim}|{url}".encode()).hexdigest()[:8]
        per_team[side] += 1
        items.append(NewsItem(
            item_id=f"w{side[0]}-{digest}", team=side, team_name=match.home.name if side == "home" else match.away.name,  # type: ignore[arg-type]
            title=claim, source=source, source_url=f"https://{host}" if host else None, link=url, published_at=published,
            age_days=age, reliability_class=classify_source(source, url), origin="web_research"))
    return items


class WebResearch:
    def __init__(self, api_key: str, model: str, base_url: str = "https://api.groq.com/openai/v1", timeout: float = 90.0,
                 transport: Optional[httpx.AsyncBaseTransport] = None):
        self.model = model
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout, headers={"Authorization": f"Bearer {api_key}"}, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def fetch(self, match: MatchSummary) -> list[NewsItem]:
        user = json.dumps({"home_team": match.home.name, "away_team": match.away.name, "competition": match.competition,
                           "kickoff_utc": match.utc_date, "today": datetime.now(timezone.utc).date().isoformat()})
        payload = {"model": self.model, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                   "tools": [{"type": "browser_search"}], "tool_choice": "required", "temperature": 0.2,
                   "max_completion_tokens": 2500, "reasoning_effort": "low"}
        try:
            resp = await self._http.post("/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise WebResearchError(f"Web research could not reach Groq ({type(exc).__name__}).") from exc
        if resp.status_code >= 400:
            try:
                detail = resp.json()["error"]["message"]
            except Exception:  # noqa: BLE001
                detail = resp.text[:160]
            hint = " (the model's per-minute token limit is too small for web browsing: use a separate Groq key via GROQ_API_KEY_4)" \
                if resp.status_code in (413, 429) else ""
            raise WebResearchError(f"Web research was refused by Groq: {detail[:200]}{hint}")
        try:
            message = resp.json()["choices"][0]["message"]
        except (KeyError, IndexError, ValueError) as exc:
            raise WebResearchError("Web research returned an unexpected response.") from exc
        return parse_findings(message, match)
