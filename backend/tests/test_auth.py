import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from auth import Authenticator, admin_secret_ok, bearer_token
from config import Settings
from errors import AuthError, ConfigError

URL = "https://proj.supabase.co"
SECRET = "s" * 40


def settings(**kw):
    return Settings(groq_keys=("a", "b", "c"), groq_models=("m",) * 3, football_api_key="f", auth_enabled=True,
                    supabase_url=URL, supabase_jwt_secret=SECRET, **kw)


def claims(**over):
    return {"sub": "11111111-1111-1111-1111-111111111111", "email": "fan@example.com", "aud": "authenticated",
            "iss": f"{URL}/auth/v1", "exp": int(time.time()) + 600, **over}


def hs(c=None, secret=SECRET):
    return jwt.encode(c or claims(), secret, algorithm="HS256")


def test_valid_hs256_token():
    u = Authenticator(settings()).verify(hs())
    assert u.id == "11111111-1111-1111-1111-111111111111" and u.email == "fan@example.com"


@pytest.mark.parametrize("bad", [
    lambda: hs(claims(exp=int(time.time()) - 5)),            # expired
    lambda: hs(claims(aud="anon")),                           # wrong audience
    lambda: hs(claims(iss="https://evil.example/auth/v1")),   # wrong issuer
    lambda: hs(secret="x" * 40),                              # wrong signature
    lambda: hs({k: v for k, v in claims().items() if k != "sub"}),
    lambda: "not-a-jwt",
    lambda: jwt.encode(claims(), None, algorithm="none"),     # unsigned token
])
def test_invalid_tokens_are_rejected(bad):
    with pytest.raises(AuthError):
        Authenticator(settings()).verify(bad())


def test_expired_message_is_friendly():
    with pytest.raises(AuthError, match="expired"):
        Authenticator(settings()).verify(hs(claims(exp=int(time.time()) - 5)))


def test_hs256_rejected_when_no_secret_is_configured():
    s = Settings(groq_keys=("a",) * 3, groq_models=("m",) * 3, football_api_key="f", auth_enabled=True, supabase_url=URL)
    with pytest.raises(AuthError):
        Authenticator(s).verify(hs())


def test_asymmetric_token_checked_against_project_keys(monkeypatch):
    key = ec.generate_private_key(ec.SECP256R1())
    authn = Authenticator(settings())
    monkeypatch.setattr(authn._jwks, "get_signing_key_from_jwt", lambda t: SimpleNamespace(key=key.public_key()))
    token = jwt.encode(claims(), key, algorithm="ES256", headers={"kid": "k1"})
    assert authn.verify(token).email == "fan@example.com"
    other = ec.generate_private_key(ec.SECP256R1())  # signed by a key that is not the project's
    with pytest.raises(AuthError):
        authn.verify(jwt.encode(claims(), other, algorithm="ES256", headers={"kid": "k1"}))


def test_algorithm_confusion_does_not_work():
    """Classic attack: sign an HS256 token with HMAC, using the project's *public* EC key (PEM) as the secret, hoping the
    server verifies HS256 with the key it uses for ES256. HS256 is only ever verified with the configured shared secret."""
    import base64, hashlib, hmac, json
    from cryptography.hazmat.primitives import serialization

    pem = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=")  # noqa: E731
    signing_input = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()) + b"." + b64(json.dumps(claims()).encode())
    forged = (signing_input + b"." + b64(hmac.new(pem, signing_input, hashlib.sha256).digest())).decode()
    with pytest.raises(AuthError):
        Authenticator(settings()).verify(forged)  # a shared secret is configured, but it is not the PEM
    no_secret = Settings(groq_keys=("a",) * 3, groq_models=("m",) * 3, football_api_key="f", auth_enabled=True, supabase_url=URL)
    with pytest.raises(AuthError):
        Authenticator(no_secret).verify(forged)


def test_jwks_outage_is_a_server_error_not_a_login_error(monkeypatch):
    authn = Authenticator(settings())

    def boom(_):
        raise jwt.PyJWKClientError("down")
    monkeypatch.setattr(authn._jwks, "get_signing_key_from_jwt", boom)
    token = jwt.encode(claims(), ec.generate_private_key(ec.SECP256R1()), algorithm="ES256")
    with pytest.raises(ConfigError):
        authn.verify(token)


def test_missing_supabase_url_fails_closed():
    s = Settings(groq_keys=("a",) * 3, groq_models=("m",) * 3, football_api_key="f", auth_enabled=True)
    with pytest.raises(ConfigError):
        Authenticator(s).verify(hs())


def test_bearer_parsing_and_admin_secret():
    assert bearer_token("Bearer abc") == "abc" and bearer_token("bearer  abc ") == "abc"
    for bad in (None, "", "abc", "Basic abc", "Bearer "):
        with pytest.raises(AuthError):
            bearer_token(bad)
    assert admin_secret_ok("s3cret", "s3cret") and not admin_secret_ok("s3cret", "nope")
    assert not admin_secret_ok("", "") and not admin_secret_ok("s3cret", None)
