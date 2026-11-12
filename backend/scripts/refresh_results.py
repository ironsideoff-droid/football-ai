"""Scheduled job (Render cron): record the final score of every predicted match that has been played.

    python -m scripts.refresh_results        (needs DATABASE_URL and FOOTBALL_API_KEY)
"""
from __future__ import annotations

import asyncio
import logging
import sys

from config import load_settings
from football_data.provider import FootballDataOrgProvider
from logging_config import setup_logging
from services.results import refresh_results

log = logging.getLogger("football_ai.refresh")


async def main() -> int:
    s = load_settings()
    setup_logging(s.log_level, s.effective_log_format)
    missing = [k for k, v in (("DATABASE_URL", s.database_url), ("FOOTBALL_API_KEY", s.football_api_key)) if not v]
    if missing:
        log.error("missing configuration", extra={"missing": missing})
        return 1
    from db import create_pool
    from evaluation.pg_store import PostgresPredictionStore

    pool = create_pool(s.database_url)
    provider = FootballDataOrgProvider(s.football_api_key, s.football_base_url, s.request_timeout)
    try:
        recorded = await refresh_results(PostgresPredictionStore(pool), provider)
        log.info("results refresh finished", extra={"recorded": len(recorded), "match_ids": recorded})
        return 0
    finally:
        await provider.aclose()
        pool.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
