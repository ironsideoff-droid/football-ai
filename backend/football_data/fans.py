"""Fan chatter from Reddit's official API (app-only OAuth). Opinion and rumour, never evidence of facts.

Only public post titles of the past week are read (no usernames, no comments, no private data). Posts are returned as NewsItems with
origin="fan_social" and reliability "low", so the agents treat them as mood/talking points and never as confirmed information.
Other networks (X, Instagram, TikTok, Facebook) have no free official API; scraping them is blocked and against their terms, so they are
not used. Any failure only skips this step.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from errors import AppError
from football_data.scout import norm_team, same_team
from schemas.match import MatchSummary
from schemas.news import NewsItem

MAX_PER_TEAM = 4
MIN_SCORE = 25  # ignore posts almost nobody engaged with
UA = "web:tactiq-football:v1.0 (read-only, public post titles)"


class FanFeedError(AppError):
    status_code, code = 502, "fan_feed_error"


def parse_posts(data: dict, team_name: str, side: str, now: Optional[datetime] = None) -> list[NewsItem]:
    now = now or datetime.now(timezone.utc)
    key = norm_team(team_name).split()
    out: list[NewsItem] = []
    seen: set[str] = set()
    for ch in (data.get("data") or {}).get("children") or []:
        d = ch.get("data") or {}
        title = str(d.get("title") or "").strip()
        link = d.get("permalink")
        if not title or not link or d.get("over_18") or d.get("stickied") or (d.get("score") or 0) < MIN_SCORE:
            continue
        if not any(k in norm_team(title).split() or same_team(title, team_name) for k in key if len(k) >= 4):
            continue  # the post must actually mention the team
        k = norm_team(title)
        if k in seen:
            continue
        seen.add(k)
        ts = d.get("created_utc")
        published = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(timespec="seconds") if ts else None
        age = round((now - datetime.fromtimestamp(float(ts), tz=timezone.utc)).total_seconds() / 86400, 2) if ts else None
        sub = d.get("subreddit_name_prefixed") or f"r/{d.get('subreddit', 'reddit')}"
        digest = hashlib.sha1(f"{side}|{link}".encode()).hexdigest()[:8]
        out.append(NewsItem(
            item_id=f"f{side[0]}-{digest}", team=side, team_name=team_name,  # type: ignore[arg-type]
            title=title[:200], source=f"{sub} · {int(d.get('score') or 0)} upvotes", source_url="https://www.reddit.com",
            link="https://www.reddit.com" + str(link), published_at=published, age_days=age, reliability_class="low", origin="fan_social"))
        if len(out) >= MAX_PER_TEAM:
            break
    return out


class RedditFans:
    def __init__(self, client_id: str, client_secret: str, timeout: float = 15.0, transport: Optional[httpx.AsyncBaseTransport] = None):
        self._auth = (client_id, client_secret)
        self._http = httpx.AsyncClient(timeout=timeout, headers={"User-Agent": UA}, transport=transport)
        self._token: Optional[tuple[str, float]] = None

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _bearer(self) -> str:
        if self._token and self._token[1] > time.monotonic() + 60:
            return self._token[0]
        r = await self._http.post("https://www.reddit.com/api/v1/access_token", auth=self._auth, data={"grant_type": "client_credentials"})
        if r.status_code >= 400 or "access_token" not in r.json():
            raise FanFeedError(f"Reddit refused the app credentials (HTTP {r.status_code}).")
        j = r.json()
        self._token = (j["access_token"], time.monotonic() + float(j.get("expires_in", 3600)))
        return self._token[0]

    async def _team(self, team: str, side: str, token: str) -> list[NewsItem]:
        r = await self._http.get("https://oauth.reddit.com/search", headers={"Authorization": f"bearer {token}"},
                                 params={"q": f'"{team}" (injury OR lineup OR "match thread" OR news OR preview)', "sort": "top", "t": "week",
                                         "limit": 25, "type": "link", "raw_json": 1})
        if r.status_code >= 400:
            raise FanFeedError(f"Reddit search failed (HTTP {r.status_code}).")
        return parse_posts(r.json(), team, side)

    async def fetch(self, match: MatchSummary) -> list[NewsItem]:
        try:
            token = await self._bearer()
            home, away = await asyncio.gather(self._team(match.home.name, "home", token), self._team(match.away.name, "away", token))
        except httpx.HTTPError as exc:
            raise FanFeedError(f"Reddit could not be reached ({type(exc).__name__}).") from exc
        return [*home, *away]
