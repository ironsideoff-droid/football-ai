"""API behaviour with authentication, subscriptions, limits and Stripe switched on. Everything external is faked."""
import hashlib
import hmac
import json
import time

import jwt
import pytest
import stripe
from fastapi.testclient import TestClient

from billing.store import MemoryBillingStore
from config import Settings
from errors import GroqRateLimitError
from evaluation.store import PredictionStore
from main import create_app
from tests.conftest import FINAL_JSON, PLAN_JSON
from tests.test_workflow_and_api import FakeProvider, build

URL, SECRET, WHSEC = "https://proj.supabase.co", "s" * 40, "whsec_test"
ALICE, BOB = "aaaaaaaa-0000-0000-0000-000000000001", "bbbbbbbb-0000-0000-0000-000000000002"


def make_settings(tmp_path, **kw):
    base = dict(groq_keys=("a", "b", "c"), groq_models=("m",) * 3, football_api_key="f", predictions_dir=str(tmp_path / "ds"),
                auth_enabled=True, database_url="postgres://unused", supabase_url=URL, supabase_jwt_secret=SECRET,
                stripe_secret_key="sk_test_x", stripe_webhook_secret=WHSEC, stripe_price_id="price_999", admin_secret="adm",
                app_url="https://app.example.com", cors_origins=("https://app.example.com",), daily_analysis_limit=30)
    return Settings(**{**base, **kw})


def token(user=ALICE, email="alice@example.com"):
    return jwt.encode({"sub": user, "email": email, "aud": "authenticated", "iss": f"{URL}/auth/v1", "exp": int(time.time()) + 600}, SECRET, algorithm="HS256")


def auth(user=ALICE):
    return {"Authorization": f"Bearer {token(user)}"}


def subscribe(billing, user, status="active", created=1000):
    billing.upsert_subscription(user, "cus_" + user[:4], "sub_1", status, None, False, created)


@pytest.fixture
def env(tmp_path, context, news_bundle):
    s = make_settings(tmp_path)
    store, billing = PredictionStore(s.predictions_dir), MemoryBillingStore()
    orch = build(context, news_bundle, store=store, chief=[PLAN_JSON, FINAL_JSON] * 4, researcher=[__import__("tests.conftest", fromlist=["x"]).RESEARCHER_JSON] * 4,
                 intel=[__import__("tests.conftest", fromlist=["x"]).INTEL_JSON] * 4)
    app = create_app(s, FakeProvider(context), orch, store=store, billing=billing)
    with TestClient(app) as client:
        yield type("Env", (), dict(client=client, settings=s, store=store, billing=billing, orch=orch, context=context))


def wait(client, job_id, headers):
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}", headers=headers).json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


# ---------------------------------------------------------------- access control
def test_endpoints_require_login(env):
    c = env.client
    for method, path in (("get", "/api/fixtures"), ("get", "/api/billing/status"), ("post", "/api/billing/checkout"),
                         ("post", "/api/analyze/start"), ("get", "/api/predictions"), ("get", "/api/jobs/x")):
        r = getattr(c, method)(path, **({"json": {"match_id": 1}} if method == "post" else {}))
        assert r.status_code == 401, path
        assert r.json()["error"]["code"] == "unauthorized"


def test_public_endpoints_and_minimal_health(env):
    assert env.client.get("/api/evaluation").status_code == 200
    body = env.client.get("/api/health").json()
    assert body == {"status": "ok"}  # nothing about configuration is exposed in production


def test_health_fails_when_production_config_is_missing(tmp_path):
    r = TestClient(create_app(make_settings(tmp_path, stripe_webhook_secret=""), billing=MemoryBillingStore())).get("/api/health")
    assert r.status_code == 503 and r.json() == {"status": "misconfigured"}


def test_api_docs_are_not_exposed_in_production(env):
    assert env.client.get("/docs").status_code == 404 and env.client.get("/openapi.json").status_code == 404


def test_free_user_can_browse_fixtures_but_not_analyze(env):
    assert env.client.get("/api/fixtures", headers=auth()).status_code == 200
    r = env.client.post("/api/analyze/start", json={"match_id": 100}, headers=auth())
    assert r.status_code == 402 and r.json()["error"]["code"] == "subscription_required"
    assert env.client.post("/api/analyze", json={"match_id": 100}, headers=auth()).status_code == 402
    assert env.client.get("/api/predictions", headers=auth()).status_code == 402
    assert env.client.get("/api/matches/100", headers=auth()).status_code == 402
    assert not env.orch.chief.client.calls  # no LLM spend for non-subscribers


@pytest.mark.parametrize("status", ["canceled", "past_due", "incomplete", "unpaid", "none"])
def test_inactive_subscription_states_are_blocked(env, status):
    subscribe(env.billing, ALICE, status)
    assert env.client.post("/api/analyze/start", json={"match_id": 100}, headers=auth()).status_code == 402


@pytest.mark.parametrize("status", ["active", "trialing"])
def test_active_states_can_analyze(env, status):
    subscribe(env.billing, ALICE, status)
    r = env.client.post("/api/analyze/start", json={"match_id": 100}, headers=auth())
    assert r.status_code == 202
    assert wait(env.client, r.json()["job_id"], auth())["status"] == "done"


def test_invalid_match_ids_rejected(env):
    subscribe(env.billing, ALICE)
    for bad in (0, -1, "abc", 10**12):
        assert env.client.post("/api/analyze/start", json={"match_id": bad}, headers=auth()).status_code == 422


# ---------------------------------------------------------------- jobs, cache, limits
def test_jobs_belong_to_their_owner(env):
    subscribe(env.billing, ALICE); subscribe(env.billing, BOB)
    job_id = env.client.post("/api/analyze/start", json={"match_id": 100}, headers=auth()).json()["job_id"]
    assert env.client.get(f"/api/jobs/{job_id}", headers=auth(BOB)).status_code == 404
    assert wait(env.client, job_id, auth())["status"] == "done"


def test_second_user_is_served_the_stored_analysis_without_llm_calls_or_allowance(env):
    subscribe(env.billing, ALICE); subscribe(env.billing, BOB)
    first = wait(env.client, env.client.post("/api/analyze/start", json={"match_id": 100}, headers=auth()).json()["job_id"], auth())
    assert first["status"] == "done" and first["result"]["prediction_record"]["status"] == "saved"
    calls = len(env.orch.chief.client.calls)
    job = env.client.post("/api/analyze/start", json={"match_id": 100}, headers=auth(BOB)).json()["job_id"]
    second = wait(env.client, job, auth(BOB))
    assert second["status"] == "done" and second["result"]["simulation"]["n_simulations"] == 50_000
    assert second["result"]["generated_at"] == first["result"]["generated_at"]  # the very same analysis
    assert len(env.orch.chief.client.calls) == calls
    assert env.client.get("/api/billing/status", headers=auth(BOB)).json()["used_today"] == 0
    assert env.client.get("/api/billing/status", headers=auth()).json()["used_today"] == 1


def test_daily_limit(tmp_path, context, news_bundle):
    s = make_settings(tmp_path, daily_analysis_limit=1)
    store, billing = PredictionStore(s.predictions_dir), MemoryBillingStore()
    subscribe(billing, ALICE)
    orch = build(context, news_bundle, store=store)
    with TestClient(create_app(s, FakeProvider(context), orch, store=store, billing=billing)) as c:
        first = c.post("/api/analyze/start", json={"match_id": 1}, headers=auth())
        assert first.status_code == 202 and wait(c, first.json()["job_id"], auth())["status"] == "done"
        r = c.post("/api/analyze/start", json={"match_id": 2}, headers=auth())
        assert r.status_code == 429 and r.json()["error"]["code"] == "usage_limit"
        st = c.get("/api/billing/status", headers=auth()).json()
        assert st["used_today"] == 1 and st["daily_limit"] == 1


def test_failed_analysis_gives_the_allowance_back(tmp_path, context, news_bundle):
    s = make_settings(tmp_path)
    store, billing = PredictionStore(s.predictions_dir), MemoryBillingStore()
    subscribe(billing, ALICE)
    orch = build(context, news_bundle, store=store, researcher=[GroqRateLimitError("slow", agent="Statistical Researcher")])
    with TestClient(create_app(s, FakeProvider(context), orch, store=store, billing=billing)) as c:
        job = c.post("/api/analyze/start", json={"match_id": 1}, headers=auth()).json()["job_id"]
        res = wait(c, job, auth())
        for _ in range(50):  # the release runs just after the job is marked finished
            if c.get("/api/billing/status", headers=auth()).json()["used_today"] == 0:
                break
            time.sleep(0.05)
        assert res["status"] == "error" and res["error"]["code"] == "groq_rate_limit"
        assert c.get("/api/billing/status", headers=auth()).json()["used_today"] == 0


def test_unexpected_errors_do_not_leak_details(env):
    subscribe(env.billing, ALICE)
    env.store.predictions = lambda: (_ for _ in ()).throw(RuntimeError("db password is hunter2"))
    r = TestClient(env.client.app, raise_server_exceptions=False).get("/api/predictions", headers=auth())
    assert r.status_code == 500 and "hunter2" not in r.text and r.json()["error"]["code"] == "internal_error"


def test_cors_allows_only_the_app_origin(env):
    ok = env.client.options("/api/fixtures", headers={"Origin": "https://app.example.com", "Access-Control-Request-Method": "GET",
                                                      "Access-Control-Request-Headers": "authorization"})
    bad = env.client.options("/api/fixtures", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "https://app.example.com"
    assert "access-control-allow-origin" not in bad.headers


# ---------------------------------------------------------------- operator endpoints
def test_admin_endpoints_need_the_secret(env):
    for method, path in (("post", "/api/evaluation/refresh"), ("get", "/api/backtest")):
        assert getattr(env.client, method)(path).status_code == 403
        assert getattr(env.client, method)(path, headers={"X-Admin-Secret": "wrong"}).status_code == 403
        assert getattr(env.client, method)(path, headers=auth()).status_code == 403  # a subscriber's login is not an admin key
    assert env.client.post("/api/evaluation/refresh", headers={"X-Admin-Secret": "adm"}).json() == {"results_recorded": []}


# ---------------------------------------------------------------- Stripe webhooks
def sign(payload: bytes, secret=WHSEC, ts=None):
    ts = ts or int(time.time())
    return f"t={ts},v1=" + hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()


def sub_event(eid, etype="customer.subscription.updated", user=ALICE, status="active", created=2000, customer="cus_A", period_end=2_000_000_000, items=False):
    obj = {"id": "sub_1", "customer": customer, "status": status, "cancel_at_period_end": False, "metadata": {"user_id": user}}
    if items:
        obj["items"] = {"data": [{"current_period_end": period_end}]}
    else:
        obj["current_period_end"] = period_end
    return {"id": eid, "type": etype, "created": created, "data": {"object": obj}}


def post_event(c, event, secret=WHSEC):
    payload = json.dumps(event).encode()
    return c.post("/api/stripe/webhook", content=payload, headers={"Stripe-Signature": sign(payload, secret), "Content-Type": "application/json"})


def test_webhook_rejects_bad_signatures(env):
    payload = json.dumps(sub_event("evt_1")).encode()
    assert env.client.post("/api/stripe/webhook", content=payload).status_code == 400
    assert post_event(env.client, sub_event("evt_1"), secret="whsec_other").status_code == 400
    old = env.client.post("/api/stripe/webhook", content=payload, headers={"Stripe-Signature": sign(payload, ts=int(time.time()) - 4000)})
    assert old.status_code == 400  # replayed, outside the tolerance window
    assert env.billing.get_subscription(ALICE) is None


def test_subscription_webhook_grants_and_revokes_access(env):
    assert env.client.post("/api/analyze/start", json={"match_id": 100}, headers=auth()).status_code == 402
    assert post_event(env.client, sub_event("evt_1", "customer.subscription.created")).json()["outcome"] == "processed"
    st = env.client.get("/api/billing/status", headers=auth()).json()
    assert st["active"] and st["status"] == "active" and st["current_period_end"].startswith("2033")
    assert env.client.post("/api/analyze/start", json={"match_id": 100}, headers=auth()).status_code == 202
    post_event(env.client, sub_event("evt_2", "customer.subscription.deleted", status="canceled", created=3000))
    assert env.client.get("/api/billing/status", headers=auth()).json()["active"] is False


def test_webhook_is_idempotent_and_ignores_stale_events(env):
    assert post_event(env.client, sub_event("evt_1", created=2000)).json()["outcome"] == "processed"
    assert post_event(env.client, sub_event("evt_1", created=2000)).json()["outcome"] == "duplicate"
    # an older event delivered late must not undo newer state
    post_event(env.client, sub_event("evt_3", status="canceled", created=3000))
    post_event(env.client, sub_event("evt_2", status="active", created=2500))
    assert env.billing.get_subscription(ALICE)["status"] == "canceled"


def test_period_end_is_read_from_items_in_newer_api_versions(env):
    post_event(env.client, sub_event("evt_1", items=True, period_end=2_100_000_000))
    assert env.billing.get_subscription(ALICE)["current_period_end"].startswith("2036")


def test_checkout_completed_links_customer_and_unknown_customers_are_ignored(env):
    ev = {"id": "evt_c", "type": "checkout.session.completed", "created": 1, "data": {"object": {"client_reference_id": BOB, "customer": "cus_B"}}}
    assert post_event(env.client, ev).status_code == 200 and env.billing.get_customer_id(BOB) == "cus_B"
    orphan = sub_event("evt_o", user=None, customer="cus_unknown"); orphan["data"]["object"]["metadata"] = {}
    assert post_event(env.client, orphan).status_code == 200  # no crash, nothing stored
    # a subscription without metadata is matched through the stored customer id
    nometa = sub_event("evt_n", customer="cus_B"); nometa["data"]["object"]["metadata"] = {}
    post_event(env.client, nometa)
    assert env.billing.get_subscription(BOB)["status"] == "active"
    assert post_event(env.client, {"id": "evt_x", "type": "invoice.paid", "created": 1, "data": {"object": {}}}).json()["outcome"] == "ignored"


def test_failed_processing_returns_500_and_can_be_retried(env, monkeypatch):
    real = env.billing.upsert_subscription
    monkeypatch.setattr(env.billing, "upsert_subscription", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    p = json.dumps(sub_event("evt_r")).encode()
    assert TestClient(env.client.app, raise_server_exceptions=False).post(
        "/api/stripe/webhook", content=p, headers={"Stripe-Signature": sign(p)}).status_code == 500
    monkeypatch.setattr(env.billing, "upsert_subscription", real)
    assert post_event(env.client, sub_event("evt_r")).json()["outcome"] == "processed"  # Stripe's retry works
    assert env.billing.get_subscription(ALICE)["status"] == "active"


# ---------------------------------------------------------------- checkout and portal
def test_checkout_session_parameters(env, monkeypatch):
    seen = {}
    monkeypatch.setattr(stripe.Customer, "create", lambda **kw: seen.setdefault("customer", kw) and {"id": "cus_new"})
    monkeypatch.setattr(stripe.checkout.Session, "create", lambda **kw: seen.update(session=kw) or {"url": "https://checkout.stripe.test/s"})
    r = env.client.post("/api/billing/checkout", headers=auth())
    assert r.json() == {"url": "https://checkout.stripe.test/s"}
    assert seen["customer"]["metadata"] == {"user_id": ALICE} and seen["customer"]["email"] == "alice@example.com"
    s = seen["session"]
    assert s["mode"] == "subscription" and s["customer"] == "cus_new" and s["client_reference_id"] == ALICE
    assert s["line_items"] == [{"price": "price_999", "quantity": 1}] and s["subscription_data"] == {"metadata": {"user_id": ALICE}}
    assert s["success_url"] == "https://app.example.com/?checkout=success" and "automatic_tax" not in s
    assert env.billing.get_customer_id(ALICE) == "cus_new"
    env.client.post("/api/billing/checkout", headers=auth())  # the stored customer is reused, not recreated
    assert seen["session"]["customer"] == "cus_new"


def test_checkout_with_tax_and_double_subscription_guard(tmp_path, monkeypatch):
    s = make_settings(tmp_path, stripe_automatic_tax=True)
    billing = MemoryBillingStore()
    seen = {}
    monkeypatch.setattr(stripe.checkout.Session, "create", lambda **kw: seen.update(kw) or {"url": "u"})
    monkeypatch.setattr(stripe.Customer, "create", lambda **kw: {"id": "cus_t"})
    with TestClient(create_app(s, store=PredictionStore(s.predictions_dir), billing=billing)) as c:
        assert c.post("/api/billing/checkout", headers=auth()).status_code == 200
        assert seen["automatic_tax"] == {"enabled": True} and seen["billing_address_collection"] == "required"
        subscribe(billing, ALICE)
        r = c.post("/api/billing/checkout", headers=auth())
        assert r.status_code == 400 and "already have an active subscription" in r.json()["error"]["message"]


def test_portal_needs_a_billing_account(env, monkeypatch):
    assert env.client.post("/api/billing/portal", headers=auth()).status_code == 400
    env.billing.set_customer_id(ALICE, "cus_p")
    monkeypatch.setattr(stripe.billing_portal.Session, "create", lambda **kw: {"url": f"https://portal.test/{kw['customer']}"})
    assert env.client.post("/api/billing/portal", headers=auth()).json() == {"url": "https://portal.test/cus_p"}


def test_billing_without_stripe_config_is_a_clear_503(tmp_path):
    s = make_settings(tmp_path, stripe_secret_key="")
    with TestClient(create_app(s, store=PredictionStore(s.predictions_dir), billing=MemoryBillingStore())) as c:
        assert c.post("/api/billing/checkout", headers=auth()).status_code == 503
