"""Scheduled job (Render cron): record the final score of every predicted match that has been played.

    python -m scripts.refresh_results        (needs DATABASE_URL and FOOTBALL_API_KEY)
"""
from __future__ import annotations

import asyncio
import logging
import sys

from config import load_settings
from football_data.provider import FootballDataOrgProvider
from services.results import refresh_results

log = logging.getLogger("football_ai.refresh")


async def main() -> int:
    s = load_settings()
    missing = [k for k, v in (("DATABASE_URL", s.database_url), ("FOOTBALL_API_KEY", s.football_api_key)) if not v]
    if missing:
        log.error("Missing configuration: %s", ", ".join(missing))
        return 1
    from db import create_pool
    from evaluation.pg_store import PostgresPredictionStore

    pool = create_pool(s.database_url)
    provider = FootballDataOrgProvider(s.football_api_key, s.football_base_url, s.request_timeout)
    try:
        recorded = await refresh_results(PostgresPredictionStore(pool), provider)
        log.info("Recorded %d new result(s): %s", len(recorded), recorded or "-")
        return 0
    finally:
        await provider.aclose()
        pool.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(asyncio.run(main()))
