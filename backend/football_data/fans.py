"""Fan chatter from X (Twitter) via the official API v2 recent search. Opinion and rumour, never evidence of facts.

Only public post text of the past week is read; the author is never requested or stored. Posts become NewsItems with
origin="fan_social" and reliability "low", so the agents treat them as mood/talking points only.
X bills per post returned (pay-per-use), so this step is cost-guarded: results are cached per match for 30 minutes, at most
MAX_PER_QUERY posts are requested per team, and a daily post budget (X_DAILY_POST_BUDGET) stops further calls once reached.
Any failure only skips this step.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from errors import AppError
from football_data.scout import norm_team, same_team
from schemas.match import MatchSummary
from schemas.news import NewsItem

MAX_PER_QUERY = 10  # the API minimum; each post returned is billed
MAX_PER_TEAM = 4
MIN_ENGAGEMENT = 10  # likes + reposts + quotes
CACHE_SECONDS = 1800
SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
SPAM = re.compile(r"giveaway|promo code|free bet|betting tips|click the link|follow (me|us) ", re.I)


class FanFeedError(AppError):
    status_code, code = 502, "fan_feed_error"


def _clean(text: str) -> str:
    text = re.sub(r"https?://\S+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_posts(data: dict, team_name: str, side: str, now: Optional[datetime] = None) -> list[NewsItem]:
    now = now or datetime.now(timezone.utc)
    key = [k for k in norm_team(team_name).split() if len(k) >= 4]
    out: list[NewsItem] = []
    seen: set[str] = set()
    for d in data.get("data") or []:
        text, pid = _clean(str(d.get("text") or "")), d.get("id")
        m = d.get("public_metrics") or {}
        likes = int(m.get("like_count") or 0)
        eng = likes + int(m.get("retweet_count") or 0) + int(m.get("quote_count") or 0)
        if not text or not pid or d.get("possibly_sensitive") or eng < MIN_ENGAGEMENT or SPAM.search(text) or text.count("#") > 3:
            continue
        if not (any(k in norm_team(text).split() for k in key) or same_team(text, team_name)):
            continue  # the post must actually mention the team
        k = norm_team(text)[:80]
        if k in seen:
            continue
        seen.add(k)
        try:
            created = datetime.fromisoformat(str(d["created_at"]).replace("Z", "+00:00"))
            published, age = created.isoformat(timespec="seconds"), round((now - created).total_seconds() / 86400, 2)
        except (KeyError, ValueError):
            published = age = None
        digest = hashlib.sha1(f"{side}|{pid}".encode()).hexdigest()[:8]
        out.append(NewsItem(
            item_id=f"f{side[0]}-{digest}", team=side, team_name=team_name,  # type: ignore[arg-type]
            title=text[:200], source=f"X · {likes} likes", source_url="https://x.com", link=f"https://x.com/i/web/status/{pid}",
            published_at=published, age_days=age, reliability_class="low", origin="fan_social"))
        if len(out) >= MAX_PER_TEAM:
            break
    return out


class XFans:
    def __init__(self, bearer_token: str, daily_post_budget: int = 600, timeout: float = 15.0,
                 transport: Optional[httpx.AsyncBaseTransport] = None, clock=time.monotonic):
        self._http = httpx.AsyncClient(timeout=timeout, headers={"Authorization": f"Bearer {bearer_token}"}, transport=transport)
        self.budget = daily_post_budget
        self._day, self._used = datetime.now(timezone.utc).date(), 0
        self._cache: dict[int, tuple[float, list[NewsItem]]] = {}
        self._clock = clock

    async def aclose(self) -> None:
        await self._http.aclose()

    def _spend(self, n: int) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self._day:
            self._day, self._used = today, 0
        self._used += n

    async def _team(self, team: str, side: str) -> list[NewsItem]:
        today = datetime.now(timezone.utc).date()
        if today != self._day:
            self._day, self._used = today, 0
        if self._used + MAX_PER_QUERY > self.budget:
            raise FanFeedError("The daily X post budget is used up; fan posts resume tomorrow.")
        r = await self._http.get(SEARCH_URL, params={
            "query": f'"{team}" (injury OR injured OR lineup OR "team news" OR preview OR suspended) -is:retweet -is:reply lang:en',
            "max_results": MAX_PER_QUERY, "sort_order": "relevancy", "tweet.fields": "created_at,public_metrics,possibly_sensitive"})
        if r.status_code in (401, 403):
            raise FanFeedError(f"X refused the credentials or the plan has no search access (HTTP {r.status_code}).")
        if r.status_code == 402:
            raise FanFeedError("The X API credit balance is empty.")
        if r.status_code == 429:
            raise FanFeedError("X rate limit reached.")
        if r.status_code >= 400:
            raise FanFeedError(f"X search failed (HTTP {r.status_code}).")
        data = r.json()
        self._spend(len(data.get("data") or []) or 1)
        return parse_posts(data, team, side)

    async def fetch(self, match: MatchSummary) -> list[NewsItem]:
        hit = self._cache.get(match.id)
        if hit and self._clock() - hit[0] < CACHE_SECONDS:
            return hit[1]
        try:
            home, away = await asyncio.gather(self._team(match.home.name, "home"), self._team(match.away.name, "away"))
        except httpx.HTTPError as exc:
            raise FanFeedError(f"X could not be reached ({type(exc).__name__}).") from exc
        items = [*home, *away]
        self._cache[match.id] = (self._clock(), items)
        return items
