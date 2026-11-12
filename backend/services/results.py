"""Record the final score of predicted matches once they have been played (used by the API and the scheduled job)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from errors import AppError
from evaluation.store import PredictionStore
from football_data.provider import FootballProvider

log = logging.getLogger("football_ai.results")


async def refresh_results(store: PredictionStore, provider: FootballProvider, now: Optional[datetime] = None) -> list[int]:
    now = now or datetime.now(timezone.utc)
    recorded: list[int] = []
    for p in await asyncio.to_thread(store.predictions):
        rec = p["record"]
        if await asyncio.to_thread(store.get_result, rec["match_id"]):
            continue
        if datetime.fromisoformat(rec["kickoff_utc"].replace("Z", "+00:00")) > now:
            continue
        try:
            ctx = await provider.get_match_context(rec["match_id"])
        except AppError as exc:
            log.warning("could not check match for a result", extra={"match_id": rec["match_id"], "error_code": exc.code})
            continue
        m = ctx.match
        if m.status == "FINISHED" and m.score_home is not None and m.score_away is not None:
            if await asyncio.to_thread(store.save_result, m.id, m.score_home, m.score_away):
                log.info("result recorded", extra={"match_id": m.id, "score": f"{m.score_home}-{m.score_away}"})
                recorded.append(m.id)
    return recorded
