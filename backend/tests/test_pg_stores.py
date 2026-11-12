"""Runs the real Postgres stores against the real migration. Skipped unless TEST_DATABASE_URL points at a server where a
scratch database may be created, e.g.  TEST_DATABASE_URL=postgresql://postgres@localhost:5432/postgres pytest"""
import asyncio
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ADMIN_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")

if ADMIN_URL:
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from billing.store import PostgresBillingStore
    from db import create_pool
    from errors import ImmutablePredictionError
    from evaluation.pg_store import PostgresPredictionStore

MIGRATION = Path(__file__).resolve().parents[2] / "supabase" / "migrations" / "20261112000000_production_schema.sql"
ALICE, BOB = str(uuid.uuid4()), str(uuid.uuid4())


@pytest.fixture(scope="module")
def pool():
    name = "football_test_" + uuid.uuid4().hex[:8]
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'create database "{name}"')
    url = make_conninfo(ADMIN_URL, dbname=name)
    with psycopg.connect(url, autocommit=True) as conn:
        # What Supabase provides out of the box: the auth schema, its users table and the anon/authenticated roles.
        conn.execute("do $$ begin if not exists (select from pg_roles where rolname='anon') then create role anon; end if; "
                     "if not exists (select from pg_roles where rolname='authenticated') then create role authenticated; end if; end $$")
        conn.execute("create schema auth; create table auth.users (id uuid primary key, email text)")
        conn.execute("insert into auth.users (id, email) values (%s, 'a@x.test'), (%s, 'b@x.test')", (ALICE, BOB))
        conn.execute(MIGRATION.read_text())
    p = create_pool(url)
    p.wait(timeout=15)
    yield p
    p.close()
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'drop database "{name}" with (force)')


def record(match_id=1, kickoff=None):
    kickoff = kickoff or (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"match_id": match_id, "kickoff_utc": kickoff, "home": "A", "away": "B", "probs": {"home": 0.5, "draw": 0.3, "away": 0.2},
            "xg_home": 1.4872345678901234, "xg_away": 1e-05, "score_matrix": [[0.1, 0.2], [0.30000000000000004, 0.4]], "unicode": "Atlético"}


# ---------------------------------------------------------------- predictions
def test_prediction_roundtrip_keeps_the_integrity_digest_valid(pool):
    store = PostgresPredictionStore(pool)
    row = store.save_prediction(record(10))
    got = store.get_prediction(10)
    assert got["record"] == row["record"] and got["digest"] == row["digest"]
    assert store.verify() == []  # floats and unicode survive jsonb unchanged, so the SHA-256 still matches
    assert [p["record"]["match_id"] for p in store.predictions()] == [10]


def test_a_prediction_is_written_once_and_never_after_kickoff(pool):
    store = PostgresPredictionStore(pool)
    store.save_prediction(record(20))
    with pytest.raises(ImmutablePredictionError, match="already exists"):
        store.save_prediction({**record(20), "home": "Changed"})
    assert store.get_prediction(20)["record"]["home"] == "A"
    with pytest.raises(ImmutablePredictionError, match="already started"):
        store.save_prediction(record(21, kickoff="2020-01-01T15:00:00Z"))
    assert store.get_prediction(21) is None


def test_concurrent_saves_of_the_same_match_store_exactly_one(pool):
    store = PostgresPredictionStore(pool)

    async def race():
        async def one(i):
            try:
                await asyncio.to_thread(store.save_prediction, {**record(30), "home": f"writer-{i}"})
                return "saved"
            except ImmutablePredictionError:
                return "refused"
        return await asyncio.gather(*(one(i) for i in range(8)))

    outcomes = asyncio.run(race())
    assert outcomes.count("saved") == 1 and outcomes.count("refused") == 7


def test_database_blocks_edits_and_deletes_even_with_direct_sql(pool):
    store = PostgresPredictionStore(pool)
    store.save_prediction(record(40)); store.save_result(40, 1, 1); store.save_analysis(40, {"x": 1})
    for sql in ("update predictions set digest = 'tampered' where match_id = 40", "delete from predictions where match_id = 40",
                "update results set home = 9 where match_id = 40", "delete from results where match_id = 40",
                "update analyses set result = '{}' where match_id = 40", "delete from analyses where match_id = 40"):
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            with pool.connection() as conn:
                conn.execute(sql)
    assert store.verify() == []


def test_results_and_joined_evaluation_rows(pool):
    store = PostgresPredictionStore(pool)
    store.save_prediction(record(50))
    assert store.save_result(50, 2, 1)["home"] == 2
    assert store.save_result(50, 0, 0) is None  # written once
    assert store.get_result(50)["away"] == 1 and store.get_result(51) is None
    joined = [r for r in store.joined() if r["match_id"] == 50]
    assert joined and joined[0]["actual"] == {"home": 2, "away": 1}


def test_analyses_are_stored_once(pool):
    store = PostgresPredictionStore(pool)
    assert store.get_analysis(60) is None
    assert store.save_analysis(60, {"a": [1, 2], "b": "Atlético"}) is True
    assert store.save_analysis(60, {"a": "other"}) is False
    assert store.get_analysis(60) == {"a": [1, 2], "b": "Atlético"}


def test_full_workflow_against_postgres(pool, context, news_bundle):
    from tests.test_workflow_and_api import build
    context.match.id = 70
    store = PostgresPredictionStore(pool)
    res = asyncio.run(build(context, news_bundle, store=store).analyze(70))
    assert res.prediction_record["status"] == "saved" and store.verify() == []
    cached = store.get_analysis(70)
    assert cached["simulation"]["n_simulations"] == 50_000 and cached["match"]["id"] == 70
    again = asyncio.run(build(context, news_bundle, store=store).analyze(70))
    assert again.prediction_record["status"] == "already_exists"


# ---------------------------------------------------------------- billing
def test_subscription_lifecycle_and_stale_events(pool):
    b = PostgresBillingStore(pool)
    assert b.get_subscription(ALICE) is None
    b.set_customer_id(ALICE, "cus_a")
    assert b.get_subscription(ALICE)["status"] == "none" and b.user_for_customer("cus_a") == ALICE and b.get_customer_id(ALICE) == "cus_a"
    end = datetime(2033, 5, 18, tzinfo=timezone.utc)
    assert b.upsert_subscription(ALICE, "cus_a", "sub_1", "active", end, False, 100) is True
    sub = b.get_subscription(ALICE)
    assert sub["status"] == "active" and sub["current_period_end"].startswith("2033-05-18") and sub["user_id"] == ALICE
    assert b.upsert_subscription(ALICE, "cus_a", "sub_1", "canceled", None, False, 300) is True
    assert b.upsert_subscription(ALICE, "cus_a", "sub_1", "active", end, False, 200) is False  # older event arrives late
    assert b.get_subscription(ALICE)["status"] == "canceled"
    assert b.upsert_subscription(ALICE, None, "sub_2", "active", end, True, 400) is True  # customer id kept when an event omits it
    assert b.get_subscription(ALICE)["stripe_customer_id"] == "cus_a" and b.get_subscription(ALICE)["cancel_at_period_end"] is True


def test_subscription_for_a_deleted_user_is_skipped_not_an_error(pool):
    assert PostgresBillingStore(pool).upsert_subscription(str(uuid.uuid4()), "cus_x", "sub_x", "active", None, False, 1) is False


def test_webhook_event_idempotency(pool):
    b = PostgresBillingStore(pool)
    assert b.record_event("evt_pg_1", "customer.subscription.updated") is True
    assert b.record_event("evt_pg_1", "customer.subscription.updated") is False
    b.forget_event("evt_pg_1")
    assert b.record_event("evt_pg_1", "customer.subscription.updated") is True


def test_daily_usage_limit_is_atomic(pool):
    b = PostgresBillingStore(pool)
    day = date(2030, 1, 1)
    assert [b.try_use(BOB, day, 2) for _ in range(3)] == [(True, 1), (True, 2), (False, 2)]
    b.release_use(BOB, day)
    assert b.used(BOB, day) == 1 and b.try_use(BOB, day, 2) == (True, 2)
    assert b.used(BOB, date(2030, 1, 2)) == 0  # a new day starts at zero
    assert b.try_use(BOB, date(2030, 1, 3), 0) == (True, 1)  # limit 0 = unlimited

    async def race():
        return await asyncio.gather(*(asyncio.to_thread(b.try_use, ALICE, day, 5) for _ in range(20)))
    results = asyncio.run(race())
    assert sum(1 for ok, _ in results if ok) == 5 and b.used(ALICE, day) == 5


def test_the_public_api_roles_cannot_touch_any_table(pool):
    tables = ["predictions", "results", "analyses", "subscriptions", "stripe_events", "analysis_usage"]
    for role in ("anon", "authenticated"):
        for t in tables:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with pool.connection() as conn:
                    conn.execute(f"set role {role}")
                    try:
                        conn.execute(f"select * from public.{t}")
                    finally:
                        conn.execute("reset role")
