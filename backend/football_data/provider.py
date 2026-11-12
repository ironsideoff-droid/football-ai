"""Football data provider interface + football-data.org implementation.

To switch provider later, implement `FootballProvider` and pass it to the app.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import date, timedelta
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from errors import (
    AppError,
    FootballAuthError,
    FootballDataError,
    FootballNotFoundError,
    FootballRateLimitError,
)
from football_data.fixtures import normalize_fixtures, normalize_match
from football_data.source_manager import api_source, assess_quality
from football_data.statistics import extract_standings, summarize_h2h, summarize_team_matches
from schemas.match import MatchContext, MatchSummary

log = logging.getLogger("football_ai.football_data")

UNAVAILABLE = {
    "injuries_suspensions": "unavailable: football-data.org does not provide injury or suspension data",
    "lineups_formations": "unavailable: no lineup or formation data is supplied",
    "team_news": "unavailable: no news feed is connected",
    "expected_goals_xg": "unavailable: no xG data is supplied",
    "advanced_stats": "unavailable: no pressing, possession or shot data is supplied",
}


MAX_RANGE_DAYS = 10  # largest date range requested from the API in one call


class FootballProvider(ABC):
    @abstractmethod
    async def list_fixtures(
        self, date_from: str, date_to: str, competitions: Optional[str] = None
    ) -> list[MatchSummary]: ...

    @abstractmethod
    async def get_match_context(self, match_id: int) -> MatchContext: ...

    async def get_competition_results(self, competition_code: str) -> list[dict]:
        raise FootballDataError("This football data provider cannot list competition results.")

    async def aclose(self) -> None:  # optional
        return None


class FootballDataOrgProvider(FootballProvider):
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.football-data.org/v4",
        timeout: float = 30.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        sleep=asyncio.sleep,
        cache_ttl: float = 300.0,
    ):
        self._http = httpx.AsyncClient(
            base_url=base_url, timeout=timeout, headers={"X-Auth-Token": api_key}, transport=transport
        )
        self._sleep = sleep
        self._ttl = cache_ttl
        self._cache: dict[tuple, tuple[float, dict]] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: Optional[dict] = None) -> dict[str, Any]:
        key = (path, tuple(sorted((params or {}).items())))
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self._ttl:
            return hit[1]
        t0 = time.monotonic()
        route = re.sub(r"\d+", "{id}", path)  # log the endpoint, not the ids
        for attempt in range(2):
            try:
                resp = await self._http.get(path, params=params)
            except httpx.HTTPError as exc:
                log.warning("football-data unreachable", extra={"route": route, "error_type": type(exc).__name__})
                raise FootballDataError(f"Could not reach the football data API: {exc}") from exc
            if resp.status_code == 429 and attempt == 0:
                found = re.search(r"(\d+)\s*seconds", resp.text)
                wait = min(int(found.group(1)) if found else 10, 30) + 1
                log.warning("football-data rate limit; waiting then retrying once", extra={"route": route, "wait_s": wait})
                await self._sleep(wait)
                continue
            break
        log.log(logging.WARNING if resp.status_code >= 400 else logging.DEBUG, "football-data call",
                extra={"route": route, "status": resp.status_code, "duration_ms": round((time.monotonic() - t0) * 1000)})
        if resp.status_code == 401:
            raise FootballAuthError("FOOTBALL_API_KEY was rejected by football-data.org.")
        if resp.status_code == 403:
            raise FootballDataError("Your football-data.org plan does not include this resource.")
        if resp.status_code == 404:
            raise FootballNotFoundError("The requested football data was not found.")
        if resp.status_code == 429:
            raise FootballRateLimitError("football-data.org rate limit reached (free tier: 10 requests/minute). Retry shortly.")
        if resp.status_code >= 400:
            raise FootballDataError(f"football-data.org returned HTTP {resp.status_code}.")
        data = resp.json()
        self._cache[key] = (time.monotonic(), data)
        return data

    async def list_fixtures(self, date_from, date_to, competitions=None):
        """Upcoming matches for any window. The API limits one request to a short date range, so the window is
        split into chunks of at most MAX_RANGE_DAYS and the results are merged (one request per chunk)."""
        start, end = date.fromisoformat(date_from), date.fromisoformat(date_to)
        found: dict[int, MatchSummary] = {}
        while start <= end:
            stop = min(start + timedelta(days=MAX_RANGE_DAYS - 1), end)
            params = {"dateFrom": start.isoformat(), "dateTo": stop.isoformat()}
            if competitions:
                params["competitions"] = competitions
            for m in normalize_fixtures(await self._get("/matches", params)):
                found[m.id] = m
            start = stop + timedelta(days=1)
        return sorted(found.values(), key=lambda m: m.utc_date)

    async def get_match_context(self, match_id: int) -> MatchContext:
        raw = await self._get(f"/matches/{match_id}")
        match = normalize_match(raw)
        hid, aid = match.home.id, match.away.id
        code = match.competition_code

        async def team_matches(tid: int) -> dict:
            return await self._get(f"/teams/{tid}/matches", {"status": "FINISHED", "limit": 100})

        results = await asyncio.gather(
            self._get(f"/matches/{match_id}/head2head", {"limit": 10}),
            team_matches(hid),
            team_matches(aid),
            self._get(f"/competitions/{code}/standings") if code else asyncio.sleep(0, {}),
            return_exceptions=True,
        )
        h2h_raw, home_raw, away_raw, stand_raw = results
        availability = dict(UNAVAILABLE)
        for name, res in (("head_to_head", h2h_raw), ("home_recent_matches", home_raw),
                          ("away_recent_matches", away_raw), ("standings", stand_raw)):
            if isinstance(res, AppError) and isinstance(res, FootballAuthError):
                raise res
            availability[name] = f"unavailable: {res.message if isinstance(res, AppError) else res}" if isinstance(res, BaseException) else "available"

        ok = lambda r: not isinstance(r, BaseException)  # noqa: E731
        base = str(self._http.base_url).rstrip("/")
        endpoints = (
            ("match", "football-data.org match", f"/matches/{match_id}", raw),
            ("h2h", "football-data.org head-to-head", f"/matches/{match_id}/head2head", h2h_raw),
            ("home_matches", "football-data.org home team results", f"/teams/{hid}/matches", home_raw),
            ("away_matches", "football-data.org away team results", f"/teams/{aid}/matches", away_raw),
            ("standings", "football-data.org standings", f"/competitions/{code}/standings", stand_raw),
        )
        sources = [
            api_source(sid, name, base + path, "ok" if ok(res) and res != {} else "failed",
                       None if ok(res) and res != {} else (res.message if isinstance(res, AppError) else "no data"))
            for sid, name, path, res in endpoints
        ]
        before = match.utc_date
        standings = extract_standings(stand_raw, hid, aid) if ok(stand_raw) and stand_raw else None
        if ok(stand_raw) and standings is None:
            availability["standings"] = "unavailable: no league table for this competition (e.g. cup or group stage)"
        context = MatchContext(
            match=match,
            standings=standings,
            home_form=summarize_team_matches(hid, match.home.name, home_raw.get("matches", []), before) if ok(home_raw) else None,
            away_form=summarize_team_matches(aid, match.away.name, away_raw.get("matches", []), before) if ok(away_raw) else None,
            head_to_head=summarize_h2h(hid, aid, h2h_raw.get("matches", [])) if ok(h2h_raw) else None,
            data_availability=availability,
            sources=sources,
            retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        context.data_quality = assess_quality(context)
        return context

    async def get_competition_results(self, competition_code: str) -> list[dict]:
        data = await self._get(f"/competitions/{competition_code}/matches", {"status": "FINISHED"})
        return sorted(data.get("matches", []), key=lambda m: m.get("utcDate", ""))
