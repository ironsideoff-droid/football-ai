import hashlib
import hmac
import json
import time

import httpx
import pytest
import stripe
from fastapi.testclient import TestClient

from billing.accounts import Accounts
from billing.stripe_service import sub_fields
from config import Settings
from main import create_app

UID = "11111111-1111-1111-1111-111111111111"


def make_app(tmp_path, sub_status=None, email="a@b.c", confirmed=True, **over):
    """App wired to a fake Supabase: /auth/v1/user and the subscriptions table."""
    db = {"subs": {}, "events": set()}
    if sub_status:
        db["subs"][UID] = {"user_id": UID, "status": sub_status, "stripe_customer_id": "cus_1", "cancel_at_period_end": False, "current_period_end": None}

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/auth/v1/user":
            return httpx.Response(200, json={"id": UID, "email": email, **({"email_confirmed_at": "2026-01-01T00:00:00Z"} if confirmed else {})}) if req.headers["authorization"] == "Bearer good-token-0123456789" else httpx.Response(401, json={})
        if path == "/rest/v1/tactiq_subscriptions":
            if req.method == "GET":
                q = req.url.params
                rows = [r for r in db["subs"].values() if ("user_id" not in q or q["user_id"] == f"eq.{r['user_id']}") and ("stripe_customer_id" not in q or q["stripe_customer_id"] == f"eq.{r['stripe_customer_id']}")]
                return httpx.Response(200, json=rows)
            body = json.loads(req.content)
            assert "service" in req.headers["apikey"]
            db["subs"].setdefault(body["user_id"], {}).update(body)
            return httpx.Response(201)
        if path == "/rest/v1/tactiq_stripe_events":
            if req.method == "GET":
                return httpx.Response(200, json=[{"id": i} for i in db["events"] if req.url.params["id"] == f"eq.{i}"])
            db["events"].add(json.loads(req.content)["id"])
            return httpx.Response(201)
        return httpx.Response(404)

    s = Settings(groq_keys=("a", "b", "c"), groq_models=("m",) * 3, football_api_key="", predictions_dir=str(tmp_path),
                 supabase_url="https://x.supabase.co", supabase_anon_key="anon-key", supabase_service_key="service-key",
                 stripe_secret_key="sk_test_x", stripe_webhook_secret="whsec_test", app_url="https://app.example", **over)
    accounts = Accounts(s, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return TestClient(create_app(s, accounts=accounts)), db


AUTH = {"Authorization": "Bearer good-token-0123456789"}


def test_public_routes_stay_open_and_data_routes_need_login(tmp_path):
    c, _ = make_app(tmp_path)
    assert c.get("/api/health").status_code == 200
    cfg = c.get("/api/config").json()
    assert cfg["price"] == {"amount": 9.99, "currency": "EUR", "interval": "month"} and "service" not in json.dumps(cfg)
    for method, path in [("get", "/api/fixtures"), ("post", "/api/analyze/start"), ("get", "/api/evaluation"), ("get", "/api/me"), ("get", "/api/jobs/x")]:
        r = getattr(c, method)(path)
        assert r.status_code == 401 and r.json()["error"]["code"] == "auth_required", path


def test_bad_token_is_rejected(tmp_path):
    c, _ = make_app(tmp_path)
    assert c.get("/api/me", headers={"Authorization": "Bearer wrong-token-0123456789"}).status_code == 401


@pytest.mark.parametrize("status", [None, "canceled", "past_due", "incomplete"])
def test_signed_in_without_active_subscription_gets_402(tmp_path, status):
    c, _ = make_app(tmp_path, sub_status=status)
    assert c.get("/api/me", headers=AUTH).json()["subscribed"] is False
    for r in (c.get("/api/fixtures", headers=AUTH), c.post("/api/analyze/start", headers=AUTH, json={"match_id": 1})):
        assert r.status_code == 402 and r.json()["error"]["code"] == "subscription_required"


@pytest.mark.parametrize("status", ["active", "trialing"])
def test_active_subscription_passes_the_gate(tmp_path, status):
    c, _ = make_app(tmp_path, sub_status=status)
    assert c.get("/api/me", headers=AUTH).json()["subscribed"] is True
    assert c.get("/api/fixtures", headers=AUTH).status_code == 503  # past the gate: now only the missing football key stops it


def test_checkout_session_is_9_99_eur_monthly_and_tied_to_the_user(tmp_path, monkeypatch):
    c, _ = make_app(tmp_path)
    seen = {}
    monkeypatch.setattr(stripe.checkout.Session, "create", lambda **kw: seen.update(kw) or {"url": "https://stripe.test/pay"})
    r = c.post("/api/billing/checkout", headers=AUTH)
    assert r.json() == {"url": "https://stripe.test/pay"}
    item = seen["line_items"][0]["price_data"]
    assert (item["unit_amount"], item["currency"], item["recurring"]) == (999, "eur", {"interval": "month"})
    assert seen["mode"] == "subscription" and seen["client_reference_id"] == UID
    assert seen["subscription_data"]["metadata"]["user_id"] == UID and seen["success_url"].startswith("https://app.example/")


def test_already_subscribed_cannot_start_a_second_checkout(tmp_path):
    c, _ = make_app(tmp_path, sub_status="active")
    assert c.post("/api/billing/checkout", headers=AUTH).status_code == 409


def _signed(payload: dict, secret="whsec_test"):
    body = json.dumps(payload).encode()
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return body, {"Stripe-Signature": f"t={ts},v1={sig}"}


def test_webhook_rejects_bad_signatures(tmp_path):
    c, db = make_app(tmp_path)
    body, _ = _signed({"id": "evt_1", "type": "customer.subscription.updated"})
    assert c.post("/api/billing/webhook", content=body, headers={"Stripe-Signature": "t=1,v1=bad"}).status_code == 400
    assert not db["subs"]


def test_webhook_activates_subscription_from_stripe_state_and_is_idempotent(tmp_path, monkeypatch):
    c, db = make_app(tmp_path)
    fake_sub = {"id": "sub_1", "customer": "cus_9", "status": "active", "cancel_at_period_end": False,
                "metadata": {"user_id": UID}, "items": {"data": [{"current_period_end": 1_800_000_000}]}}
    calls = []
    monkeypatch.setattr(stripe.Subscription, "retrieve", lambda sid, **kw: calls.append(sid) or fake_sub)
    event = {"id": "evt_1", "type": "customer.subscription.updated", "object": "event", "data": {"object": {"id": "sub_1"}}}
    body, headers = _signed(event)
    assert c.post("/api/billing/webhook", content=body, headers=headers).json()["outcome"] == "processed"
    assert db["subs"][UID]["status"] == "active" and db["subs"][UID]["stripe_customer_id"] == "cus_9"
    assert db["subs"][UID]["current_period_end"].startswith("2027-")
    assert c.get("/api/me", headers=AUTH).json()["subscribed"] is True
    assert c.post("/api/billing/webhook", content=body, headers=headers).json()["outcome"] == "duplicate" and calls == ["sub_1"]
    # cancellation comes through the same path and removes access
    fake_sub["status"] = "canceled"
    body2, h2 = _signed({**event, "id": "evt_2", "type": "customer.subscription.deleted"})
    c.post("/api/billing/webhook", content=body2, headers=h2)
    assert c.get("/api/fixtures", headers=AUTH).status_code == 402


def test_unrelated_webhook_events_are_ignored(tmp_path):
    c, db = make_app(tmp_path)
    body, h = _signed({"id": "evt_3", "type": "invoice.created", "data": {"object": {}}})
    assert c.post("/api/billing/webhook", content=body, headers=h).json()["outcome"] == "ignored" and not db["subs"]


def test_period_end_is_read_from_either_stripe_layout():
    base = {"id": "s", "customer": "c", "status": "active"}
    assert sub_fields({**base, "current_period_end": 1_800_000_000})["current_period_end"].startswith("2027")
    assert sub_fields({**base, "items": {"data": [{"current_period_end": 1_800_000_000}]}})["current_period_end"].startswith("2027")
    assert sub_fields(base)["current_period_end"] is None


def test_production_fails_closed_when_accounts_are_not_configured(tmp_path):
    s = Settings(groq_keys=("a",) * 3, groq_models=("m",) * 3, football_api_key="f", predictions_dir=str(tmp_path))
    c = TestClient(create_app(s))
    assert c.get("/api/fixtures").status_code == 401
    assert c.get("/api/fixtures", headers=AUTH).status_code == 503


def test_admin_uses_the_app_without_paying(tmp_path):
    c, _ = make_app(tmp_path, email="Ironsideoff@Gmail.com", admin_emails=("ironsideoff@gmail.com",))
    me = c.get("/api/me", headers=AUTH).json()
    assert me["is_admin"] is True and me["subscribed"] is True
    assert c.get("/api/fixtures", headers=AUTH).status_code == 503  # past the gate with no subscription row
    assert c.post("/api/billing/checkout", headers=AUTH).status_code == 409


def test_admin_email_must_be_verified(tmp_path):
    c, _ = make_app(tmp_path, email="ironsideoff@gmail.com", confirmed=False, admin_emails=("ironsideoff@gmail.com",))
    assert c.get("/api/me", headers=AUTH).json()["is_admin"] is False
    assert c.get("/api/fixtures", headers=AUTH).status_code == 402


def test_other_users_are_not_admins(tmp_path):
    c, _ = make_app(tmp_path, email="someone@else.com", admin_emails=("ironsideoff@gmail.com",))
    assert c.get("/api/me", headers=AUTH).json()["is_admin"] is False
    assert c.get("/api/fixtures", headers=AUTH).status_code == 402
