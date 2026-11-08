"""Football data provider interface + football-data.org implementation.

To switch provider later, implement `FootballProvider` and pass it to the app.
"""
from __future__ import annotations

import asyncio
import re
import time
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
from football_data.statistics import extract_standings, summarize_h2h, summarize_team_matches
from schemas.match import MatchContext, MatchSummary

UNAVAILABLE = {
    "injuries_suspensions": "unavailable: football-data.org does not provide injury or suspension data",
    "lineups_formations": "unavailable: no lineup or formation data is supplied",
    "team_news": "unavailable: no news feed is connected",
    "expected_goals_xg": "unavailable: no xG data is supplied",
    "advanced_stats": "unavailable: no pressing, possession or shot data is supplied",
}


class FootballProvider(ABC):
    @abstractmethod
    async def list_fixtures(
        self, date_from: str, date_to: str, competitions: Optional[str] = None
    ) -> list[MatchSummary]: ...

    @abstractmethod
    async def get_match_context(self, match_id: int) -> MatchContext: ...

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
        for attempt in range(2):
            try:
                resp = await self._http.get(path, params=params)
            except httpx.HTTPError as exc:
                raise FootballDataError(f"Could not reach the football data API: {exc}") from exc
            if resp.status_code == 429 and attempt == 0:
                found = re.search(r"(\d+)\s*seconds", resp.text)
                await self._sleep(min(int(found.group(1)) if found else 10, 30) + 1)
                continue
            break
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
        params = {"dateFrom": date_from, "dateTo": date_to}
        if competitions:
            params["competitions"] = competitions
        return normalize_fixtures(await self._get("/matches", params))

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
        before = match.utc_date
        standings = extract_standings(stand_raw, hid, aid) if ok(stand_raw) and stand_raw else None
        if ok(stand_raw) and standings is None:
            availability["standings"] = "unavailable: no league table for this competition (e.g. cup or group stage)"
        return MatchContext(
            match=match,
            standings=standings,
            home_form=summarize_team_matches(hid, match.home.name, home_raw.get("matches", []), before) if ok(home_raw) else None,
            away_form=summarize_team_matches(aid, match.away.name, away_raw.get("matches", []), before) if ok(away_raw) else None,
            head_to_head=summarize_h2h(hid, aid, h2h_raw.get("matches", [])) if ok(h2h_raw) else None,
            data_availability=availability,
            retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
