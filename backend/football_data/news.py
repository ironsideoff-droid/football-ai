"""News retrieval for the Football Intelligence agent.

The default provider reads Google News RSS search results (no API key). Results are headlines with their publisher
and publication time, nothing more: the agent never sees article bodies, so it cannot be given (or invent) quotes
that are not literally present in a headline. Replace `NewsProvider` to use a different feed.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import quote_plus

import httpx

from football_data.source_manager import classify_source, news_freshness, now_iso
from schemas.match import MatchSummary
from schemas.news import NewsBundle, NewsItem

MAX_ITEMS_PER_TEAM = 10
TOPICS = ("", "injury OR injured OR suspended OR doubt OR ruled out", "team news OR lineup OR starting XI OR press conference")
MAX_AGE_DAYS = 14


class NewsProvider(ABC):
    @abstractmethod
    async def fetch(self, match: MatchSummary) -> NewsBundle: ...

    async def aclose(self) -> None:
        return None


class DisabledNews(NewsProvider):
    async def fetch(self, match: MatchSummary) -> NewsBundle:
        return NewsBundle(status="disabled", retrieved_at=now_iso(), provider="disabled")


def parse_rss(xml_text: str, team: str, team_name: str, now: Optional[datetime] = None) -> list[NewsItem]:
    now = now or datetime.now(timezone.utc)
    root = ET.fromstring(xml_text)
    items: list[NewsItem] = []
    seen: set[str] = set()
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        src_node = node.find("source")
        source = (src_node.text or "").strip() if src_node is not None and src_node.text else ""
        source_url = src_node.get("url") if src_node is not None else None
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3].rstrip()
        key = re.sub(r"\W+", " ", title.lower())
        if not title or not link or key in seen:
            continue
        seen.add(key)
        published = None
        try:
            published = parsedate_to_datetime(node.findtext("pubDate") or "").astimezone(timezone.utc).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            pass
        _, age = news_freshness(published, now)
        if age is not None and age > MAX_AGE_DAYS:
            continue
        digest = hashlib.sha1(f"{team}|{title}|{link}".encode()).hexdigest()[:8]
        items.append(NewsItem(
            item_id=f"{team[0]}-{digest}", team=team, team_name=team_name, title=title,  # type: ignore[arg-type]
            source=source or "unknown", source_url=source_url, link=link, published_at=published, age_days=age,
            reliability_class=classify_source(source, source_url),
        ))
    items.sort(key=lambda i: i.published_at or "", reverse=True)
    return items[:MAX_ITEMS_PER_TEAM]


class GoogleNewsRssProvider(NewsProvider):
    def __init__(self, timeout: float = 15.0, transport: Optional[httpx.AsyncBaseTransport] = None,
                 base_url: str = "https://news.google.com"):
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport, follow_redirects=True,
                                       headers={"User-Agent": "football-ai/2.0"})

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _query(self, side: str, name: str, topic: str) -> list[NewsItem]:
        query = quote_plus(f'"{name}" football {topic} when:{MAX_AGE_DAYS}d'.replace("  ", " "))
        resp = await self._http.get(f"/rss/search?q={query}&hl=en-GB&gl=GB&ceid=GB:en")
        resp.raise_for_status()
        return parse_rss(resp.text, side, name)

    async def _team(self, side: str, name: str) -> list[NewsItem]:
        """Three searches per team (general, injuries and suspensions, team news and lineups), merged and de-duplicated."""
        results = await asyncio.gather(*(self._query(side, name, t) for t in TOPICS), return_exceptions=True)
        good = [r for r in results if not isinstance(r, BaseException)]
        if not good:
            raise next(r for r in results if isinstance(r, BaseException))
        merged: dict[str, NewsItem] = {}
        for items in good:
            for it in items:
                merged.setdefault(it.item_id, it)
        return sorted(merged.values(), key=lambda i: i.published_at or "", reverse=True)[:MAX_ITEMS_PER_TEAM]

    async def fetch(self, match: MatchSummary) -> NewsBundle:
        results = await asyncio.gather(
            self._team("home", match.home.name), self._team("away", match.away.name), return_exceptions=True
        )
        items: list[NewsItem] = []
        errors: list[str] = []
        for side, res in zip(("home", "away"), results):
            if isinstance(res, BaseException):
                errors.append(f"{side} team news failed: {type(res).__name__}: {res}")
            else:
                items.extend(res)
        failed = len(errors)
        status = "unavailable" if failed == 2 else "partial" if failed == 1 else "ok"
        return NewsBundle(status=status, items=items, errors=errors, retrieved_at=now_iso(), provider="Google News RSS")
