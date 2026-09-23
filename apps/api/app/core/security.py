"""Authentication primitives: password hashing, JWTs and API keys.

Raw API keys are shown exactly once, at creation. Only an HMAC digest is stored, so a
database leak does not hand an attacker working credentials.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
from jose import JWTError, jwt

from common.errors import AuthenticationError
from common.settings import Settings, get_settings

BCRYPT_ROUNDS = 12

API_KEY_PREFIX = "mk"
TokenType = Literal["access", "refresh"]


# ------------------------------------------------------------------ passwords


def _prepare(password: str) -> bytes:
    """bcrypt silently truncates at 72 bytes, so hash first and encode the digest."""
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    return bcrypt.hashpw(_prepare(password), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_prepare(password), password_hash.encode("utf-8"))
    except (ValueError, TypeError):  # malformed stored hash
        return False


# ----------------------------------------------------------------------- JWT


def create_token(
    subject: str,
    *,
    token_type: TokenType = "access",
    extra_claims: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> str:
    """Issue a signed token.

    Callers pass ``ver`` in ``extra_claims`` (the user's ``token_version``): bumping that
    column invalidates every token already issued, which is what "sign out everywhere" and
    a password change both need.
    """
    settings = settings or get_settings()
    now = datetime.now(UTC)
    lifetime = (
        timedelta(minutes=settings.jwt_access_token_minutes)
        if token_type == "access"
        else timedelta(days=settings.jwt_refresh_token_days)
    )
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + lifetime).timestamp()),
        "jti": secrets.token_urlsafe(8),
        **(extra_claims or {}),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(
    token: str, *, expected_type: TokenType = "access", settings: Settings | None = None
) -> dict[str, Any]:
    settings = settings or get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise AuthenticationError("Invalid or expired token.") from exc
    if payload.get("type") != expected_type:
        raise AuthenticationError(f"Expected a {expected_type} token.")
    return payload


# ------------------------------------------------------------------ API keys


def generate_api_key(environment: str = "live") -> str:
    return f"{API_KEY_PREFIX}_{environment}_{secrets.token_urlsafe(32)}"


def generate_invitation_token() -> str:
    """Single-use token emailed to an invitee. Only its hash is stored."""
    return secrets.token_urlsafe(32)


def hash_invitation_token(token: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return hmac.new(
        settings.api_key_secret.encode("utf-8"),
        f"invitation:{token.strip()}".encode(),
        hashlib.sha256,
    ).hexdigest()


def hash_api_key(api_key: str, settings: Settings | None = None) -> str:
    """HMAC rather than a bare hash, so the digest is useless without the server secret."""
    settings = settings or get_settings()
    return hmac.new(
        settings.api_key_secret.encode("utf-8"),
        api_key.strip().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def api_key_prefix(api_key: str) -> str:
    """Display prefix, e.g. ``mk_live_ZmFr…`` — enough to recognise, not enough to use."""
    return api_key[:16]


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)
