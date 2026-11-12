"""Verification of Supabase Auth access tokens (JWTs).

New Supabase projects sign tokens with an asymmetric key (ES256/RS256) published at
`<SUPABASE_URL>/auth/v1/.well-known/jwks.json`; older projects use a shared HS256 secret (SUPABASE_JWT_SECRET).
Both are supported. The algorithm is never taken on trust: HS256 is accepted only when a secret is configured, and
asymmetric tokens are checked against the project's published keys only, so a forged `alg` header gets nothing.
"""
from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from typing import Optional

import jwt
from jwt import PyJWKClient

from config import Settings
from errors import AuthError, ConfigError


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: Optional[str] = None


log = logging.getLogger("football_ai.auth")

DEV_USER = AuthUser(id="00000000-0000-0000-0000-000000000000", email="dev@localhost")


class Authenticator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.issuer = f"{settings.supabase_url}/auth/v1" if settings.supabase_url else ""
        self._jwks = PyJWKClient(f"{self.issuer}/.well-known/jwks.json", cache_keys=True, lifespan=600, timeout=10) if self.issuer else None

    def verify(self, token: str) -> AuthUser:
        if not self.issuer:
            raise ConfigError("SUPABASE_URL is not configured on the server.")
        try:
            alg = jwt.get_unverified_header(token).get("alg")
            if alg == "HS256":
                if not self.settings.supabase_jwt_secret:
                    log.warning("token rejected", extra={"reason": "hs256_without_configured_secret"})
                    raise AuthError("Invalid token.")
                key = self.settings.supabase_jwt_secret
            elif alg in ("ES256", "RS256"):
                assert self._jwks is not None
                key = self._jwks.get_signing_key_from_jwt(token).key
            else:
                log.warning("token rejected", extra={"reason": "unsupported_algorithm", "alg": str(alg)[:20]})
                raise AuthError("Invalid token.")
            claims = jwt.decode(token, key, algorithms=[alg], audience="authenticated", issuer=self.issuer,
                                options={"require": ["exp", "sub"]})
        except AuthError:
            raise
        except jwt.ExpiredSignatureError as exc:
            log.info("token rejected", extra={"reason": "expired"})
            raise AuthError("Your session has expired. Please sign in again.") from exc
        except jwt.PyJWKClientError as exc:  # signing keys could not be fetched: our side, not the user's
            log.error("could not load Supabase signing keys", extra={"error_type": type(exc).__name__})
            raise ConfigError("Could not load the identity provider's signing keys. Try again shortly.") from exc
        except jwt.InvalidTokenError as exc:
            log.warning("token rejected", extra={"reason": "invalid", "error_type": type(exc).__name__})
            raise AuthError("Invalid token.") from exc
        return AuthUser(id=str(claims["sub"]), email=claims.get("email"))


def bearer_token(authorization: Optional[str]) -> str:
    if not authorization:
        raise AuthError("Sign in to continue.")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("Invalid Authorization header.")
    return token.strip()


def admin_secret_ok(expected: str, supplied: Optional[str]) -> bool:
    return bool(expected) and bool(supplied) and hmac.compare_digest(expected.encode(), supplied.encode())
