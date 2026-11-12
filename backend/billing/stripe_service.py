"""Stripe Checkout, Customer Portal and webhook handling for the monthly subscription.

Source of truth for access is the `subscriptions` table, written only by verified Stripe webhooks. Redirecting back
from Checkout never grants access by itself; the UI waits for the webhook to land.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import stripe

from auth import AuthUser
from billing.store import BillingStore, is_active
from config import Settings
from errors import BillingError, ConfigError

log = logging.getLogger("football_ai.billing")

SUBSCRIPTION_EVENTS = {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}


class BillingService:
    def __init__(self, settings: Settings, store: BillingStore):
        self.settings, self.store = settings, store

    def _require_stripe(self) -> None:
        if not (self.settings.stripe_secret_key and self.settings.stripe_price_id):
            raise ConfigError("Billing is not configured on the server (STRIPE_SECRET_KEY / STRIPE_PRICE_ID).")

    # ------------------------------------------------------------------ checkout and portal (blocking: call in a thread)
    def create_checkout(self, user: AuthUser) -> str:
        self._require_stripe()
        key = self.settings.stripe_secret_key
        if is_active(self.store.get_subscription(user.id)):
            raise BillingError("You already have an active subscription. Use “Manage billing” to change it.")
        customer = self.store.get_customer_id(user.id)
        if not customer:
            customer = stripe.Customer.create(email=user.email, metadata={"user_id": user.id}, api_key=key)["id"]
            self.store.set_customer_id(user.id, customer)
        params: dict = dict(
            mode="subscription", customer=customer, client_reference_id=user.id,
            line_items=[{"price": self.settings.stripe_price_id, "quantity": 1}],
            success_url=f"{self.settings.app_url}/?checkout=success", cancel_url=f"{self.settings.app_url}/?checkout=cancelled",
            allow_promotion_codes=True, subscription_data={"metadata": {"user_id": user.id}},
        )
        if self.settings.stripe_automatic_tax:  # EU VAT etc.; needs Stripe Tax set up in the dashboard
            params.update(automatic_tax={"enabled": True}, billing_address_collection="required",
                          customer_update={"address": "auto", "name": "auto"})
        url = stripe.checkout.Session.create(api_key=key, **params)["url"]
        log.info("checkout session created", extra={"user_id": user.id, "automatic_tax": self.settings.stripe_automatic_tax})
        return url

    def create_portal(self, user: AuthUser) -> str:
        self._require_stripe()
        customer = self.store.get_customer_id(user.id)
        if not customer:
            raise BillingError("There is no billing account for this user yet.")
        url = stripe.billing_portal.Session.create(customer=customer, return_url=self.settings.app_url, api_key=self.settings.stripe_secret_key)["url"]
        log.info("billing portal session created", extra={"user_id": user.id})
        return url

    # ------------------------------------------------------------------ webhook (blocking)
    def handle_webhook(self, payload: bytes, signature: Optional[str]) -> str:
        """Returns 'processed', 'duplicate' or 'ignored'. Raises BillingError for a bad signature (HTTP 400) and any other
        exception for a failure that Stripe should retry (HTTP 500)."""
        if not self.settings.stripe_webhook_secret:
            raise ConfigError("STRIPE_WEBHOOK_SECRET is not configured on the server.")
        try:
            stripe.Webhook.construct_event(payload, signature or "", self.settings.stripe_webhook_secret)
        except (ValueError, stripe.SignatureVerificationError) as exc:
            log.warning("stripe webhook rejected", extra={"reason": "invalid_signature_or_payload", "error_type": type(exc).__name__})
            raise BillingError("Invalid webhook signature.") from exc
        event = json.loads(payload)
        event_id, etype = event["id"], event["type"]
        if etype != "checkout.session.completed" and etype not in SUBSCRIPTION_EVENTS:
            log.debug("stripe webhook ignored", extra={"event_id": event_id, "event_type": etype})
            return "ignored"
        if not self.store.record_event(event_id, etype):
            log.info("stripe webhook", extra={"event_id": event_id, "event_type": etype, "outcome": "duplicate"})
            return "duplicate"
        try:
            self._apply(etype, event["data"]["object"], int(event.get("created", 0)))
        except Exception:
            log.exception("stripe webhook failed; Stripe will retry", extra={"event_id": event_id, "event_type": etype})
            self.store.forget_event(event_id)  # let Stripe's retry run it again
            raise
        log.info("stripe webhook", extra={"event_id": event_id, "event_type": etype, "outcome": "processed"})
        return "processed"

    def _apply(self, etype: str, obj: dict, created: int) -> None:
        if etype == "checkout.session.completed":
            if obj.get("client_reference_id") and obj.get("customer"):
                self.store.set_customer_id(obj["client_reference_id"], obj["customer"])
            return
        customer = obj.get("customer")
        user_id = (obj.get("metadata") or {}).get("user_id") or (self.store.user_for_customer(customer) if customer else None)
        if not user_id:
            log.warning("stripe subscription has no known user; ignored", extra={"subscription_id": obj.get("id"), "customer_id": customer})
            return
        applied = self.store.upsert_subscription(
            user_id, customer, obj.get("id"), obj.get("status", "none"), _period_end(obj),
            bool(obj.get("cancel_at_period_end")), created)
        log.info("subscription updated" if applied else "subscription event skipped (older than stored state)",
                 extra={"user_id": user_id, "subscription_id": obj.get("id"), "status": obj.get("status"), "cancel_at_period_end": bool(obj.get("cancel_at_period_end"))})


def _period_end(sub: dict) -> Optional[datetime]:
    """current_period_end moved from the subscription to its items in newer Stripe API versions; accept both."""
    ts = sub.get("current_period_end")
    if ts is None:
        items = (sub.get("items") or {}).get("data") or []
        ts = max((i.get("current_period_end") for i in items if i.get("current_period_end")), default=None)
    return datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
