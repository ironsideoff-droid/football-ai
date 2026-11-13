"""Stripe Checkout, customer portal and webhook handling (subscription state is always re-read from Stripe)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

import stripe

from billing.accounts import Accounts, User
from config import Settings
from errors import AppError, ConfigError

HANDLED = ("checkout.session.completed", "customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted")


def _period_end(sub) -> Optional[str]:
    ts = sub.get("current_period_end")
    if ts is None:  # newer API versions keep it on the subscription items
        items = (sub.get("items") or {}).get("data") or []
        ts = items[0].get("current_period_end") if items else None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None


def sub_fields(sub) -> dict:
    return {
        "stripe_customer_id": sub["customer"],
        "stripe_subscription_id": sub["id"],
        "status": sub["status"],
        "current_period_end": _period_end(sub),
        "cancel_at_period_end": bool(sub.get("cancel_at_period_end")),
    }


SAAS_PERSONAL_TAX_CODE = "txcd_10103000"


class Billing:
    def __init__(self, settings: Settings, accounts: Accounts):
        self.s, self.accounts = settings, accounts

    def _key(self) -> str:
        if not self.s.stripe_secret_key:
            raise ConfigError("STRIPE_SECRET_KEY is missing.")
        return self.s.stripe_secret_key

    def _base(self, origin: str) -> str:
        return self.s.app_url or origin.rstrip("/")

    async def checkout_url(self, user: User, origin: str) -> str:
        key, base = self._key(), self._base(origin)
        sub = await self.accounts.get_subscription(user)
        line = ({"price": self.s.stripe_price_id, "quantity": 1} if self.s.stripe_price_id else {
            "quantity": 1,
            "price_data": {"currency": self.s.price_currency, "unit_amount": self.s.price_cents, "recurring": {"interval": "month"},
                           "product_data": {"name": "TACTIQ — monthly access",
                                            # Required by Stripe Managed Payments: SaaS, personal use.
                                            "tax_code": SAAS_PERSONAL_TAX_CODE}}})
        params: dict = {
            "mode": "subscription", "line_items": [line], "client_reference_id": user.id,
            "metadata": {"user_id": user.id}, "subscription_data": {"metadata": {"user_id": user.id}},
            "success_url": f"{base}/?checkout=success", "cancel_url": f"{base}/?checkout=cancelled",
            "allow_promotion_codes": True,
        }
        if sub.get("stripe_customer_id"):
            params["customer"] = sub["stripe_customer_id"]
        elif user.email:
            params["customer_email"] = user.email
        try:
            session = await asyncio.to_thread(lambda: stripe.checkout.Session.create(api_key=key, **params))
        except stripe.StripeError as exc:
            raise AppError(f"Stripe could not start the checkout: {exc.user_message or type(exc).__name__}") from exc
        return session["url"]

    async def portal_url(self, user: User, origin: str) -> str:
        key = self._key()
        customer = (await self.accounts.get_subscription(user)).get("stripe_customer_id")
        if not customer:
            raise AppError("There is no subscription to manage yet.")
        try:
            sess = await asyncio.to_thread(lambda: stripe.billing_portal.Session.create(
                api_key=key, customer=customer, return_url=self._base(origin)))
        except stripe.StripeError as exc:
            raise AppError(f"Stripe could not open the billing portal: {exc.user_message or type(exc).__name__}") from exc
        return sess["url"]

    async def sync(self, user: User) -> dict:
        """Fallback if the webhook is late/misconfigured: find the user's subscription in Stripe."""
        key = self._key()
        try:
            res = await asyncio.to_thread(lambda: stripe.Subscription.search(
                api_key=key, query=f"metadata['user_id']:'{user.id}'", limit=5))
        except stripe.StripeError as exc:
            raise AppError("Could not check Stripe for your subscription.") from exc
        subs = sorted(res["data"], key=lambda x: x["created"], reverse=True)
        best = next((x for x in subs if x["status"] in ("active", "trialing")), subs[0] if subs else None)
        if best:
            await self.accounts.upsert_subscription(user.id, sub_fields(best))
        return await self.accounts.get_subscription(user)

    async def handle_webhook(self, payload: bytes, signature: Optional[str]) -> str:
        if not self.s.stripe_webhook_secret:
            raise ConfigError("STRIPE_WEBHOOK_SECRET is missing.")
        try:
            event = stripe.Webhook.construct_event(payload, signature or "", self.s.stripe_webhook_secret)
        except (ValueError, stripe.SignatureVerificationError) as exc:
            raise WebhookRejected("Invalid Stripe signature.") from exc
        if event["type"] not in HANDLED:
            return "ignored"
        if await self.accounts.event_seen(event["id"]):
            return "duplicate"
        obj, key = event["data"]["object"], self._key()
        sub_id = obj.get("subscription") if event["type"] == "checkout.session.completed" else obj["id"]
        if not sub_id:
            return "ignored"
        # Re-read the current state instead of trusting event order.
        sub = await asyncio.to_thread(lambda: stripe.Subscription.retrieve(sub_id, api_key=key))
        user_id = (sub.get("metadata") or {}).get("user_id") or obj.get("client_reference_id") \
            or await self.accounts.user_for_customer(sub["customer"])
        if not user_id:
            return "no_user"
        await self.accounts.upsert_subscription(user_id, sub_fields(sub))
        await self.accounts.record_event(event["id"], event["type"])
        return "processed"


class WebhookRejected(AppError):
    status_code, code = 400, "invalid_webhook"
