"""Subscription state, Stripe webhook idempotency and per-user usage counters.

`MemoryBillingStore` is used for local development and tests; `PostgresBillingStore` in production.
Subscription dicts: {user_id, stripe_customer_id, stripe_subscription_id, status, current_period_end (ISO or None),
cancel_at_period_end}.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timezone
from typing import Optional

from errors import StoreError

ACTIVE_STATUSES = frozenset({"active", "trialing"})


def is_active(subscription: Optional[dict]) -> bool:
    return bool(subscription) and subscription.get("status") in ACTIVE_STATUSES


class BillingStore:
    def get_subscription(self, user_id: str) -> Optional[dict]: raise NotImplementedError
    def set_customer_id(self, user_id: str, customer_id: str) -> None: raise NotImplementedError
    def user_for_customer(self, customer_id: str) -> Optional[str]: raise NotImplementedError
    def upsert_subscription(self, user_id: str, customer_id: Optional[str], subscription_id: Optional[str], status: str,
                            period_end: Optional[datetime], cancel_at_period_end: bool, event_created: int) -> bool:
        """Store the state. Returns False (and changes nothing) if a newer webhook event was already applied."""
        raise NotImplementedError
    def record_event(self, event_id: str, event_type: str) -> bool:
        """True if this Stripe event id is new (and is now recorded), False if it was already processed."""
        raise NotImplementedError
    def forget_event(self, event_id: str) -> None: raise NotImplementedError
    def try_use(self, user_id: str, day: date, limit: int) -> tuple[bool, int]:
        """Atomically count one analysis for `day`. Returns (allowed, count_after). limit <= 0 means unlimited."""
        raise NotImplementedError
    def release_use(self, user_id: str, day: date) -> None: raise NotImplementedError
    def used(self, user_id: str, day: date) -> int: raise NotImplementedError

    def get_customer_id(self, user_id: str) -> Optional[str]:
        sub = self.get_subscription(user_id)
        return sub.get("stripe_customer_id") if sub else None


class MemoryBillingStore(BillingStore):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: dict[str, dict] = {}
        self._created: dict[str, int] = {}
        self._events: set[str] = set()
        self._usage: dict[tuple[str, date], int] = {}

    def get_subscription(self, user_id):
        with self._lock:
            s = self._subs.get(user_id)
            return dict(s) if s else None

    def set_customer_id(self, user_id, customer_id):
        with self._lock:
            s = self._subs.setdefault(user_id, {"user_id": user_id, "stripe_subscription_id": None, "status": "none",
                                                "current_period_end": None, "cancel_at_period_end": False})
            s["stripe_customer_id"] = customer_id

    def user_for_customer(self, customer_id):
        with self._lock:
            return next((u for u, s in self._subs.items() if s.get("stripe_customer_id") == customer_id), None)

    def upsert_subscription(self, user_id, customer_id, subscription_id, status, period_end, cancel_at_period_end, event_created):
        with self._lock:
            if event_created < self._created.get(user_id, 0):
                return False
            s = self._subs.setdefault(user_id, {"user_id": user_id})
            s.update(stripe_customer_id=customer_id or s.get("stripe_customer_id"), stripe_subscription_id=subscription_id, status=status,
                     current_period_end=period_end.isoformat() if period_end else None, cancel_at_period_end=cancel_at_period_end)
            self._created[user_id] = event_created
            return True

    def record_event(self, event_id, event_type):
        with self._lock:
            if event_id in self._events:
                return False
            self._events.add(event_id)
            return True

    def forget_event(self, event_id):
        with self._lock:
            self._events.discard(event_id)

    def try_use(self, user_id, day, limit):
        with self._lock:
            n = self._usage.get((user_id, day), 0)
            if limit > 0 and n >= limit:
                return False, n
            self._usage[(user_id, day)] = n + 1
            return True, n + 1

    def release_use(self, user_id, day):
        with self._lock:
            if self._usage.get((user_id, day), 0) > 0:
                self._usage[(user_id, day)] -= 1

    def used(self, user_id, day):
        with self._lock:
            return self._usage.get((user_id, day), 0)


class PostgresBillingStore(BillingStore):
    def __init__(self, pool):
        self.pool = pool

    def _run(self, sql: str, params: tuple = (), fetch: bool = True):
        import psycopg
        from psycopg.rows import dict_row
        try:
            with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                return cur.fetchall() if fetch else None
        except psycopg.errors.ForeignKeyViolation:
            raise
        except (psycopg.Error, OSError) as exc:
            raise StoreError(f"Database error: {type(exc).__name__}") from exc

    @staticmethod
    def _shape(r: dict) -> dict:
        pe = r.get("current_period_end")
        return {**r, "user_id": str(r["user_id"]), "current_period_end": pe.astimezone(timezone.utc).isoformat() if pe else None}

    def get_subscription(self, user_id):
        rows = self._run("select user_id, stripe_customer_id, stripe_subscription_id, status, current_period_end, cancel_at_period_end "
                         "from subscriptions where user_id = %s", (user_id,))
        return self._shape(rows[0]) if rows else None

    def set_customer_id(self, user_id, customer_id):
        self._run("insert into subscriptions (user_id, stripe_customer_id) values (%s, %s) "
                  "on conflict (user_id) do update set stripe_customer_id = excluded.stripe_customer_id, updated_at = now()",
                  (user_id, customer_id), fetch=False)

    def user_for_customer(self, customer_id):
        rows = self._run("select user_id from subscriptions where stripe_customer_id = %s", (customer_id,))
        return str(rows[0]["user_id"]) if rows else None

    def upsert_subscription(self, user_id, customer_id, subscription_id, status, period_end, cancel_at_period_end, event_created):
        import psycopg
        try:
            rows = self._run(
                "insert into subscriptions (user_id, stripe_customer_id, stripe_subscription_id, status, current_period_end, "
                "cancel_at_period_end, stripe_event_created) values (%s, %s, %s, %s, %s, %s, %s) "
                "on conflict (user_id) do update set "
                "stripe_customer_id = coalesce(excluded.stripe_customer_id, subscriptions.stripe_customer_id), "
                "stripe_subscription_id = excluded.stripe_subscription_id, status = excluded.status, "
                "current_period_end = excluded.current_period_end, cancel_at_period_end = excluded.cancel_at_period_end, "
                "stripe_event_created = excluded.stripe_event_created, updated_at = now() "
                "where subscriptions.stripe_event_created <= excluded.stripe_event_created returning user_id",
                (user_id, customer_id, subscription_id, status, period_end, cancel_at_period_end, event_created))
        except psycopg.errors.ForeignKeyViolation:  # the Supabase user was deleted; nothing to attach the subscription to
            return False
        return bool(rows)

    def record_event(self, event_id, event_type):
        return bool(self._run("insert into stripe_events (id, type) values (%s, %s) on conflict (id) do nothing returning id",
                              (event_id, event_type)))

    def forget_event(self, event_id):
        # stripe_events is not append-only: a failed delivery must be retryable.
        self._run("delete from stripe_events where id = %s", (event_id,), fetch=False)

    def try_use(self, user_id, day, limit):
        rows = self._run(
            "insert into analysis_usage (user_id, day, count) values (%s, %s, 1) "
            "on conflict (user_id, day) do update set count = analysis_usage.count + 1 "
            "where %s <= 0 or analysis_usage.count < %s returning count", (user_id, day, limit, limit))
        if rows:
            return True, rows[0]["count"]
        return False, self.used(user_id, day)

    def release_use(self, user_id, day):
        self._run("update analysis_usage set count = greatest(count - 1, 0) where user_id = %s and day = %s", (user_id, day), fetch=False)

    def used(self, user_id, day):
        rows = self._run("select count from analysis_usage where user_id = %s and day = %s", (user_id, day))
        return rows[0]["count"] if rows else 0
