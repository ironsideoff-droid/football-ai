"""PostgreSQL connection pool (Supabase). Imported only when DATABASE_URL is configured."""
from __future__ import annotations

from psycopg_pool import ConnectionPool


def create_pool(database_url: str, max_size: int = 10) -> ConnectionPool:
    """Open a pool without blocking start-up: if the database is briefly unreachable the app still boots and
    individual requests fail with a clear error until it is back.

    * autocommit: every store method is a single statement, so no transaction is ever left open.
    * prepare_threshold=None: required by Supabase's transaction pooler (port 6543), which does not support
      server-side prepared statements.
    """
    pool = ConnectionPool(
        database_url, min_size=1, max_size=max_size, open=False, name="football-ai",
        kwargs={"autocommit": True, "prepare_threshold": None, "connect_timeout": 10},
        check=ConnectionPool.check_connection,
    )
    pool.open(wait=False)
    return pool
