"""Logging: format, secret redaction, request ids, and that real request flows never leak secrets or personal data."""
import asyncio
import io
import json
import logging
import time

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from billing.store import MemoryBillingStore
from config import Settings
from errors import GroqRateLimitError
from evaluation.store import PredictionStore
from logging_config import JsonFormatter, clean_request_id, redact, request_id_var, setup_logging
from main import create_app
from services.groq_client import GroqClient
from tests.conftest import FINAL_JSON, INTEL_JSON, PLAN_JSON, RESEARCHER_JSON
from tests.test_production_api import ALICE, BOB, auth, make_settings, post_event, sub_event, subscribe, token, wait
from tests.test_workflow_and_api import FakeProvider, build


def capture(level="DEBUG", fmt="json"):
    buf = io.StringIO()
    setup_logging(level, fmt, buf)
    return buf


def lines(buf):
    return [json.loads(l) for l in buf.getvalue().splitlines() if l.strip()]


def find(buf, msg, **match):
    return [l for l in lines(buf) if l["msg"] == msg and all(l.get(k) == v for k, v in match.items())]


@pytest.fixture(autouse=True)
def _restore_logging():
    yield
    setup_logging("INFO", "text", io.StringIO())  # never leave a closed pytest stream installed as a handler


# ---------------------------------------------------------------- redaction
@pytest.mark.parametrize("text, secret", [
    ("Authorization: Bearer abc.def-ghi_123", "abc.def-ghi_123"),
    ("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig_nature-1 end", "eyJhbGciOiJIUzI1NiJ9"),
    ("stripe sk_live_51HabcDEF123 key", "sk_live_51HabcDEF123"),
    ("stripe rk_test_abcdef123456", "rk_test_abcdef123456"),
    ("hook whsec_AbCdEf0123456789", "whsec_AbCdEf0123456789"),
    ("groq gsk_AbCdEf0123456789xyz", "gsk_AbCdEf0123456789xyz"),
    ("connect postgresql://postgres.ref:S3cr3t!pw@aws-0.pooler.supabase.com:6543/postgres", "S3cr3t!pw"),
    ("password=hunter2 next", "hunter2"),
    ('{"api_key": "abc123xyz"}', "abc123xyz"),
    ("X-Auth-Token: footballtoken99", "footballtoken99"),
    ("user fan.name+tag@example.co.uk signed in", "fan.name+tag@example.co.uk"),
])
def test_secrets_are_redacted(text, secret):
    assert secret not in redact(text)


@pytest.mark.parametrize("text", [
    "step done step=researcher duration_ms=1234", "max_tokens=3500 prompt_tokens=812 completion_tokens=401",
    "match_id=12345 status=200 route=/matches/{id}", "Chelsea v Arsenal kickoff 2026-11-14T15:00:00Z",
])
def test_ordinary_text_is_left_alone(text):
    assert redact(text) == text


def test_json_lines_are_valid_complete_and_scrubbed():
    buf = capture()
    log = logging.getLogger("football_ai.test")
    tok = request_id_var.set("req-abcdef12")
    try:
        log.info("hello alice@example.com", extra={"user_id": "u-1", "dsn": "postgres://u:pw123@h/db", "n": 3, "ok": True, "items": ["Bearer xyz.abc"]})
        try:
            raise RuntimeError("boom password=hunter2")
        except RuntimeError:
            log.exception("failed")
    finally:
        request_id_var.reset(tok)
    first, second = lines(buf)
    assert first["msg"] == "hello [email]" and first["level"] == "INFO" and first["logger"] == "football_ai.test"
    assert first["request_id"] == "req-abcdef12" and first["user_id"] == "u-1" and first["n"] == 3 and first["ok"] is True
    assert "pw123" not in json.dumps(first) and "xyz.abc" not in json.dumps(first)
    assert first["ts"].endswith("+00:00")
    assert "RuntimeError" in second["exc"] and "hunter2" not in second["exc"]


def test_text_format_is_readable():
    buf = capture("INFO", "text")
    logging.getLogger("football_ai.test").warning("careful", extra={"match_id": 7})
    out = buf.getvalue()
    assert "WARNING" in out and "football_ai.test: careful" in out and "match_id=7" in out


def test_setup_is_idempotent_and_respects_level():
    capture("INFO"); buf = capture("WARNING")
    log = logging.getLogger("football_ai.test")
    log.info("hidden"); log.warning("shown")
    assert [l["msg"] for l in lines(buf)] == ["shown"]  # one handler, not two; INFO filtered out


def test_request_id_validation():
    assert clean_request_id("abc-12345_X.y") == "abc-12345_X.y"
    for bad in (None, "", "short", "has space in it", "line\nbreak-injection", "x" * 65, "<script>alert(1)</script>"):
        assert clean_request_id(bad) is None


def test_settings_auto_format_and_level_fallback(monkeypatch):
    base = dict(groq_keys=("a",) * 3, groq_models=("m",) * 3, football_api_key="f")
    assert Settings(**base, auth_enabled=True).effective_log_format == "json"
    assert Settings(**base, auth_enabled=False).effective_log_format == "text"
    assert Settings(**base, auth_enabled=True, log_format="text").effective_log_format == "text"
    assert Settings(**base, auth_enabled=False, log_format="bogus").effective_log_format == "text"
    setup_logging("NOT_A_LEVEL", "text", io.StringIO())
    assert logging.getLogger("football_ai").level == logging.INFO


# ---------------------------------------------------------------- HTTP request logging
@pytest.fixture
def prod(tmp_path, context, news_bundle):
    s = make_settings(tmp_path)
    store, billing = PredictionStore(s.predictions_dir), MemoryBillingStore()
    orch = build(context, news_bundle, store=store, chief=[PLAN_JSON, FINAL_JSON] * 3, researcher=[RESEARCHER_JSON] * 3, intel=[INTEL_JSON] * 3)
    app = create_app(s, FakeProvider(context), orch, store=store, billing=billing)  # create_app installs logging...
    buf = capture()                                                                 # ...so capture after it
    with TestClient(app) as client:
        yield type("P", (), dict(client=client, billing=billing, buf=buf, store=store, settings=s))


def test_every_request_is_logged_with_a_request_id_and_no_query_string(prod):
    r = prod.client.get("/api/fixtures?days=3&competitions=PL", headers=auth())
    rid = r.headers["X-Request-ID"]
    (entry,) = find(prod.buf, "request", path="/api/fixtures")
    assert entry["method"] == "GET" and entry["status"] == 200 and entry["user_id"] == ALICE
    assert entry["request_id"] == rid and isinstance(entry["duration_ms"], int) and entry["duration_ms"] >= 0
    assert "days=3" not in prod.buf.getvalue() and "competitions" not in prod.buf.getvalue()


def test_caller_supplied_request_id_is_kept_only_when_safe(prod):
    ok = prod.client.get("/api/evaluation", headers={"X-Request-ID": "trace-0123456789"})
    assert ok.headers["X-Request-ID"] == "trace-0123456789"
    evil = prod.client.get("/api/evaluation", headers={"X-Request-ID": "x y\\n{\"level\":\"FATAL\"}"})
    assert evil.headers["X-Request-ID"] != "x y\\n{\"level\":\"FATAL\"}" and len(evil.headers["X-Request-ID"]) == 16


def test_health_checks_are_quiet_at_info_but_failures_are_not(tmp_path):
    s = make_settings(tmp_path)
    app = create_app(s, store=PredictionStore(s.predictions_dir), billing=MemoryBillingStore())
    quiet = capture("INFO")
    with TestClient(app) as c:
        c.get("/api/health")
    assert not find(quiet, "request", path="/api/health")
    bad = make_settings(tmp_path, stripe_webhook_secret="")
    app2 = create_app(bad, store=PredictionStore(bad.predictions_dir), billing=MemoryBillingStore())
    buf = capture("INFO")
    with TestClient(app2) as c:
        assert c.get("/api/health").status_code == 503
    assert find(buf, "request", path="/api/health", status=503)
    assert any(l["msg"] == "Configuration problem: STRIPE_WEBHOOK_SECRET is not set" or "STRIPE_WEBHOOK_SECRET" in json.dumps(l) for l in lines(buf))


def test_startup_summary_has_no_secrets(tmp_path):
    s = make_settings(tmp_path)
    app = create_app(s, store=PredictionStore(s.predictions_dir), billing=MemoryBillingStore())
    buf = capture("INFO")
    with TestClient(app):
        pass
    (start,) = find(buf, "starting")
    assert start["auth_enabled"] is True and start["storage"] == "files" and start["log_format"] == "json"
    for secret in (s.stripe_secret_key, s.stripe_webhook_secret, s.supabase_jwt_secret, s.admin_secret, s.database_url):
        assert secret not in buf.getvalue()


def test_auth_failures_are_logged_without_the_token(prod):
    expired = jwt.encode({"sub": ALICE, "aud": "authenticated", "iss": "https://proj.supabase.co/auth/v1", "exp": int(time.time()) - 5,
                          "email": "alice@example.com"}, "s" * 40, algorithm="HS256")
    assert prod.client.get("/api/fixtures", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    forged = token().rsplit(".", 1)[0] + ".AAAA"
    assert prod.client.get("/api/fixtures", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    assert find(prod.buf, "token rejected", reason="expired", level="INFO")
    assert find(prod.buf, "token rejected", reason="invalid", level="WARNING")
    out = prod.buf.getvalue()
    assert expired not in out and forged not in out and "eyJ" not in out and "alice@example.com" not in out


def test_admin_denials_are_logged_and_never_the_secret(prod):
    prod.client.post("/api/evaluation/refresh", headers={"X-Admin-Secret": "guess-123"})
    assert find(prod.buf, "admin access denied", reason="wrong_secret", level="WARNING")
    assert "guess-123" not in prod.buf.getvalue()


def test_an_analysis_can_be_followed_through_the_logs_by_request_id(prod):
    subscribe(prod.billing, ALICE)
    r = prod.client.post("/api/analyze/start", json={"match_id": 100}, headers=auth())
    rid = r.headers["X-Request-ID"]
    assert wait(prod.client, r.json()["job_id"], auth())["status"] == "done"
    mine = [l for l in lines(prod.buf) if l.get("request_id") == rid]
    msgs = [l["msg"] for l in mine]
    for expected in ("job queued", "job started", "analysis started", "step done", "analysis finished", "job finished"):
        assert expected in msgs, f"{expected} missing from {msgs}"
    done = next(l for l in mine if l["msg"] == "analysis finished")
    assert done["prediction_record"] == "saved" and done["abstained"] is False and isinstance(done["duration_ms"], int)
    fin = next(l for l in mine if l["msg"] == "job finished")
    assert fin["status"] == "done" and fin["user_id"] == ALICE and fin["total_ms"] >= 0
    steps = {l["step"] for l in mine if l["msg"] == "step done"}
    assert {"data", "researcher", "intelligence", "chief_plan"} & steps
    # a second user opening the same match is logged as a cache hit
    prod.billing.upsert_subscription(BOB, "cus_b", "sub_b", "active", None, False, 1)
    prod.client.post("/api/analyze/start", json={"match_id": 100}, headers=auth(BOB))
    assert find(prod.buf, "analysis served from stored copy", user_id=BOB)


def test_failed_job_is_logged_as_a_warning_with_the_error_code(tmp_path, context, news_bundle):
    s = make_settings(tmp_path)
    store, billing = PredictionStore(s.predictions_dir), MemoryBillingStore()
    subscribe(billing, ALICE)
    orch = build(context, news_bundle, store=store, researcher=[GroqRateLimitError("slow", agent="Statistical Researcher")])
    app = create_app(s, FakeProvider(context), orch, store=store, billing=billing)
    buf = capture()
    with TestClient(app) as c:
        job = c.post("/api/analyze/start", json={"match_id": 1}, headers=auth()).json()["job_id"]
        assert wait(c, job, auth())["status"] == "error"
    failed = find(buf, "job failed")
    assert failed and failed[0]["error_code"] == "groq_rate_limit" and failed[0]["level"] == "WARNING" and failed[0]["agent"] == "Statistical Researcher"
    assert find(buf, "analysis failed", error_code="groq_rate_limit")


def test_unhandled_errors_log_a_traceback_and_return_the_request_id(prod):
    subscribe(prod.billing, ALICE)
    prod.store.predictions = lambda: (_ for _ in ()).throw(RuntimeError("db password=hunter2 down"))
    r = TestClient(prod.client.app, raise_server_exceptions=False).get("/api/predictions", headers=auth())
    assert r.status_code == 500
    body_rid = r.json()["error"]["request_id"]
    (crash,) = find(prod.buf, "unhandled error")
    assert crash["level"] == "ERROR" and crash["request_id"] == body_rid and "RuntimeError" in crash["exc"]
    assert "hunter2" not in prod.buf.getvalue() and "hunter2" not in r.text
    assert find(prod.buf, "request", path="/api/predictions", status=500, level="ERROR")


def test_server_side_app_errors_are_logged_but_client_errors_are_not_noisy(tmp_path):
    s = make_settings(tmp_path, stripe_secret_key="")
    app = create_app(s, store=PredictionStore(s.predictions_dir), billing=MemoryBillingStore())
    buf = capture("INFO")
    with TestClient(app) as c:
        assert c.post("/api/billing/checkout", headers=auth()).status_code == 503
        assert c.get("/api/fixtures").status_code == 401
    assert find(buf, "server-side error", error_code="configuration_error", level="WARNING")
    assert not [l for l in lines(buf) if l["msg"] == "server-side error" and l.get("error_code") == "unauthorized"]


def test_stripe_webhook_outcomes_are_logged_without_signatures(prod):
    ev = sub_event("evt_log_1", "customer.subscription.created")
    assert post_event(prod.client, ev).json()["outcome"] == "processed"
    assert post_event(prod.client, ev).json()["outcome"] == "duplicate"
    assert post_event(prod.client, sub_event("evt_log_2"), secret="whsec_wrong").status_code == 400
    assert find(prod.buf, "stripe webhook", event_id="evt_log_1", outcome="processed", event_type="customer.subscription.created")
    assert find(prod.buf, "stripe webhook", event_id="evt_log_1", outcome="duplicate")
    assert find(prod.buf, "stripe webhook rejected", level="WARNING")
    assert find(prod.buf, "subscription updated", user_id=ALICE, status="active")
    out = prod.buf.getvalue()
    assert "v1=" not in out and "whsec_" not in out and "alice@example.com" not in out


# ---------------------------------------------------------------- upstream clients
def groq(handler, **kw):
    async def nosleep(_): return None
    return GroqClient("Researcher", "gsk_SECRETKEY123", "llama-x", transport=httpx.MockTransport(handler), sleep=nosleep, **kw)


def test_groq_calls_log_timing_and_token_counts_but_never_content_or_keys():
    buf = capture()
    ok = {"choices": [{"message": {"content": '{"answer": "TOP-SECRET-MODEL-OUTPUT"}'}}], "usage": {"prompt_tokens": 812, "completion_tokens": 401}}
    out = asyncio.run(groq(lambda r: httpx.Response(200, json=ok)).chat_json("SYSTEM-PROMPT-TEXT", "USER-PROMPT-TEXT"))
    assert out["answer"] == "TOP-SECRET-MODEL-OUTPUT"
    (entry,) = find(buf, "groq call")
    assert entry["agent"] == "Researcher" and entry["model"] == "llama-x" and entry["prompt_tokens"] == 812 and entry["completion_tokens"] == 401
    for secret in ("SYSTEM-PROMPT-TEXT", "USER-PROMPT-TEXT", "TOP-SECRET-MODEL-OUTPUT", "gsk_SECRETKEY123"):
        assert secret not in buf.getvalue()


def test_groq_retries_and_give_ups_are_logged():
    buf = capture()
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "1"}, json={"error": {"message": "slow down"}})
    with pytest.raises(GroqRateLimitError):
        asyncio.run(groq(handler).chat_json("s", "u"))
    assert len(find(buf, "groq retry", status=429)) == 2 and find(buf, "groq gave up", status=429, attempts=3)
    assert find(buf, "groq retry")[0]["level"] == "WARNING"


def test_groq_rejected_key_is_an_error_log_without_the_key():
    buf = capture()
    from errors import GroqAuthError
    with pytest.raises(GroqAuthError):
        asyncio.run(groq(lambda r: httpx.Response(401, json={"error": {"message": "bad key gsk_SECRETKEY123"}})).chat_json("s", "u"))
    assert find(buf, "groq rejected the API key", status=401, level="ERROR") and "gsk_SECRETKEY123" not in buf.getvalue()


def test_football_data_calls_log_route_not_ids_and_warn_on_errors():
    from football_data.provider import FootballDataOrgProvider
    buf = capture()

    async def nosleep(_): return None

    def handler(req):
        return httpx.Response(200, json={"matches": []}) if req.url.path.endswith("/matches") else httpx.Response(404, json={})
    p = FootballDataOrgProvider("tok-FOOTBALLSECRET", transport=httpx.MockTransport(handler), sleep=nosleep)
    asyncio.run(p._get("/matches", {"dateFrom": "2026-11-12"}))
    with pytest.raises(Exception):
        asyncio.run(p._get("/matches/4242"))
    ok = find(buf, "football-data call", route="/matches", status=200)
    missing = find(buf, "football-data call", route="/matches/{id}", status=404)
    assert ok and ok[0]["level"] == "DEBUG" and missing and missing[0]["level"] == "WARNING"
    assert "4242" not in buf.getvalue() and "FOOTBALLSECRET" not in buf.getvalue()
