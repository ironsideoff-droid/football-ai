"""Supabase auth (token verification) and subscription storage (PostgREST)."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional

import httpx

from config import Settings
from errors import AppError, AuthRequiredError, ConfigError

ACTIVE_STATUSES = ("active", "trialing")


@dataclass(frozen=True)
class User:
    id: str
    email: str
    token: str = ""


class Accounts:
    """Verifies Supabase access tokens and reads/writes the `subscriptions` table."""

    def __init__(self, settings: Settings, client: Optional[httpx.AsyncClient] = None):
        self.s = settings
        self.http = client or httpx.AsyncClient(timeout=15.0)
        self._cache: dict[str, tuple[float, User]] = {}

    async def aclose(self) -> None:
        await self.http.aclose()

    # -------------------------------------------------------------- auth
    async def verify(self, token: str) -> User:
        if not (self.s.supabase_url and self.s.supabase_anon_key):
            raise ConfigError("Accounts are not configured (SUPABASE_URL / SUPABASE_ANON_KEY missing).")
        hit = self._cache.get(token)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        try:
            r = await self.http.get(
                f"{self.s.supabase_url}/auth/v1/user",
                headers={"apikey": self.s.supabase_anon_key, "Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise AppError(f"Could not reach the login service ({type(exc).__name__}).") from exc
        if r.status_code in (401, 403):
            raise AuthRequiredError("Your session has expired. Please sign in again.")
        if r.status_code >= 400:
            raise AppError("The login service returned an error. Try again in a moment.")
        body = r.json()
        user = User(id=body["id"], email=body.get("email") or "", token=token)
        if len(self._cache) > 2000:
            self._cache.clear()
        self._cache[token] = (time.monotonic() + 60, user)
        return user

    # ----------------------------------------------------- subscriptions
    def _service_headers(self) -> dict:
        if not self.s.supabase_service_key:
            raise ConfigError("SUPABASE_SERVICE_KEY is missing; subscription state cannot be saved.")
        k = self.s.supabase_service_key
        return {"apikey": k, "Authorization": f"Bearer {k}", "Content-Type": "application/json"}

    async def get_subscription(self, user: User) -> dict:
        """Reads the user's own row with their own token (row-level security applies)."""
        r = await self.http.get(
            f"{self.s.supabase_url}/rest/v1/tactiq_subscriptions",
            params={"select": "status,current_period_end,cancel_at_period_end,stripe_customer_id", "user_id": f"eq.{user.id}"},
            headers={"apikey": self.s.supabase_anon_key, "Authorization": f"Bearer {user.token}"},
        )
        if r.status_code >= 400:
            raise AppError("Could not read your subscription status.")
        rows = r.json()
        return rows[0] if rows else {"status": "none", "current_period_end": None, "cancel_at_period_end": False, "stripe_customer_id": None}

    async def upsert_subscription(self, user_id: str, fields: dict) -> None:
        r = await self.http.post(
            f"{self.s.supabase_url}/rest/v1/tactiq_subscriptions",
            params={"on_conflict": "user_id"},
            headers={**self._service_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={"user_id": user_id, **fields, "updated_at": datetime.now(timezone.utc).isoformat()},
        )
        if r.status_code >= 400:
            raise AppError(f"Saving the subscription failed ({r.status_code}).")

    async def user_for_customer(self, customer_id: str) -> Optional[str]:
        r = await self.http.get(
            f"{self.s.supabase_url}/rest/v1/tactiq_subscriptions",
            params={"select": "user_id", "stripe_customer_id": f"eq.{customer_id}"},
            headers=self._service_headers(),
        )
        rows = r.json() if r.status_code < 400 else []
        return rows[0]["user_id"] if rows else None

    async def event_seen(self, event_id: str) -> bool:
        r = await self.http.get(
            f"{self.s.supabase_url}/rest/v1/tactiq_stripe_events", params={"select": "id", "id": f"eq.{event_id}"},
            headers=self._service_headers(),
        )
        return bool(r.status_code < 400 and r.json())

    async def record_event(self, event_id: str, etype: str) -> None:
        await self.http.post(
            f"{self.s.supabase_url}/rest/v1/tactiq_stripe_events",
            headers={**self._service_headers(), "Prefer": "resolution=ignore-duplicates,return=minimal"},
            json={"id": event_id, "type": etype},
        )


def is_active(sub: dict) -> bool:
    return sub.get("status") in ACTIVE_STATUSES
