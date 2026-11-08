import asyncio
import json

import httpx
import pytest

from config import Settings, load_settings
from errors import AgentOutputError, GroqAuthError, GroqRateLimitError, GroqServiceError
from services.groq_client import GroqClient, extract_json


def client(handler, key="k", **kw):
    async def nosleep(_):
        return None
    return GroqClient("Researcher", key, "m", transport=httpx.MockTransport(handler), sleep=nosleep, **kw)


def ok(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_success_and_request_shape():
    seen = {}

    def handler(req):
        seen["auth"], seen["body"] = req.headers["authorization"], json.loads(req.content)
        return ok('{"a": 1}')

    assert asyncio.run(client(handler, key="secret-1").chat_json("sys", "usr")) == {"a": 1}
    assert seen["auth"] == "Bearer secret-1"
    assert seen["body"]["model"] == "m" and seen["body"]["response_format"] == {"type": "json_object"}


def test_each_agent_uses_its_own_key():
    keys = []
    handler = lambda req: (keys.append(req.headers["authorization"]), ok("{}"))[1]  # noqa: E731
    for k in ("key-a", "key-b", "key-c"):
        asyncio.run(client(handler, key=k).chat_json("s", "u"))
    assert keys == ["Bearer key-a", "Bearer key-b", "Bearer key-c"]


def test_invalid_key():
    with pytest.raises(GroqAuthError):
        asyncio.run(client(lambda r: httpx.Response(401, json={})).chat_json("s", "u"))


def test_rate_limit_retries_then_fails():
    calls = []
    def handler(req):
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "1"})
    with pytest.raises(GroqRateLimitError):
        asyncio.run(client(handler).chat_json("s", "u"))
    assert len(calls) == 3


def test_rate_limit_recovers():
    seq = iter([httpx.Response(429), ok("{}")])
    assert asyncio.run(client(lambda r: next(seq)).chat_json("s", "u")) == {}


def test_server_and_bad_request_errors():
    with pytest.raises(GroqServiceError):
        asyncio.run(client(lambda r: httpx.Response(503), max_retries=0).chat_json("s", "u"))
    bad = httpx.Response(400, json={"error": {"message": "model not found"}})
    with pytest.raises(GroqServiceError, match="model not found"):
        asyncio.run(client(lambda r: bad).chat_json("s", "u"))


def test_non_json_output():
    with pytest.raises(AgentOutputError):
        asyncio.run(client(lambda r: ok("not json at all")).chat_json("s", "u"))


def test_extract_json_handles_fences():
    assert extract_json('```json\n{"x": 2}\n```') == {"x": 2}


def test_settings_from_env(monkeypatch):
    for i, k in enumerate(("A", "B", "C"), 1):
        monkeypatch.setenv(f"GROQ_API_KEY_{i}", k)
    monkeypatch.setenv("GROQ_MODEL_2", "model-two")
    monkeypatch.setenv("FOOTBALL_API_KEY", "f")
    s = load_settings()
    assert s.groq_keys == ("A", "B", "C") and s.groq_models[1] == "model-two"
    assert s.groq_models[0] == s.groq_models[2] and s.missing_groq() == []


def test_missing_and_duplicate_keys():
    s = Settings(groq_keys=("a", "a", ""), groq_models=("m",) * 3, football_api_key="")
    assert s.missing_groq() == ["GROQ_API_KEY_3"] and s.duplicate_key_warning()
