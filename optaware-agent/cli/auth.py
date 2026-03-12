"""OptAware authentication helpers — Phase 8.

Provides:

* ``APIKeyAuth``     — FastAPI dependency that validates the ``X-API-Key`` header.
* ``verify_api_key`` — Constant-time comparison of a presented key against the
                       configured secret.
* ``generate_api_key`` — Generates cryptographically random API keys.
* ``JWTAuth``        — JWT token generation and validation (HS256, pure-stdlib).
* ``create_token``   — Standalone JWT creation function.
* ``verify_token``   — Standalone JWT verification function.

Secret resolution order (highest wins):
  1. ``OPTAWARE_API_KEY`` environment variable.
  2. ``portal.secret_key`` from the loaded OptAware config.
  3. Empty string → authentication disabled (development mode only; every
     non-empty key is accepted and a warning is logged).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from typing import Any, Optional

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_API_KEY_HEADER_NAME = "X-API-Key"
_JWT_ALGORITHM = "HS256"
_KEY_BYTES = 32  # 256 bits → 64 hex characters

# Reusable FastAPI header scheme (shows up in Swagger UI as "apiKey").
_api_key_scheme = APIKeyHeader(name=_API_KEY_HEADER_NAME, auto_error=False)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_secret() -> str:
    """Return the active signing / API secret from the environment or config.

    Checks, in order:
    1. ``OPTAWARE_API_KEY`` environment variable.
    2. ``portal.secret_key`` from the loaded OptAware config singleton.
    3. Falls back to an empty string (open / dev mode).
    """
    env_key = os.environ.get("OPTAWARE_API_KEY", "")
    if env_key:
        return env_key

    try:
        from config.loader import get_config  # noqa: PLC0415

        cfg = get_config()
        if cfg.portal.secret_key:
            return cfg.portal.secret_key
    except Exception:
        pass

    logger.warning(
        "No API key / secret configured.  "
        "Set OPTAWARE_API_KEY or configure portal.secret_key in optaware.yaml."
    )
    return ""


def _b64url_encode(data: bytes) -> str:
    """URL-safe base64 encode without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    """URL-safe base64 decode, adding missing padding."""
    rem = len(s) % 4
    if rem:
        s += "=" * (4 - rem)
    return base64.urlsafe_b64decode(s)


# ---------------------------------------------------------------------------
# API-key public API
# ---------------------------------------------------------------------------


def generate_api_key() -> str:
    """Generate a cryptographically random API key.

    Returns:
        A 64-character lowercase hex string prefixed with ``oaw_``
        (288 bits of entropy).
    """
    return f"oaw_{secrets.token_hex(_KEY_BYTES)}"


def verify_api_key(key: str) -> bool:
    """Return ``True`` when *key* matches the configured secret.

    The comparison uses :func:`hmac.compare_digest` on the SHA-256 hashes of
    both values to prevent timing side-channel attacks.

    Args:
        key: The key string presented by the caller.

    Returns:
        ``True`` if valid, ``False`` otherwise.
    """
    if not key:
        return False

    expected = _resolve_secret()
    if not expected:
        # Dev mode: no secret configured → accept any non-empty key.
        logger.debug("verify_api_key: no secret configured; accepting key (dev mode).")
        return True

    # Compare SHA-256 digests to avoid leaking key length via compare_digest.
    return hmac.compare_digest(
        hashlib.sha256(key.encode()).digest(),
        hashlib.sha256(expected.encode()).digest(),
    )


# ---------------------------------------------------------------------------
# FastAPI API-key dependency
# ---------------------------------------------------------------------------


class APIKeyAuth:
    """FastAPI dependency: validate ``X-API-Key`` on every protected request.

    Usage::

        from fastapi import Depends
        from cli.auth import APIKeyAuth

        auth = APIKeyAuth()

        @app.get("/api/protected")
        async def route(_: str = Depends(auth)):
            ...

    Args:
        auto_error: When ``True`` (default) the dependency raises
            :class:`~fastapi.HTTPException` 401/403 on failure.  Set to
            ``False`` to return ``None`` instead (optional auth).
    """

    def __init__(self, auto_error: bool = True) -> None:
        self._auto_error = auto_error

    async def __call__(
        self,
        api_key: Optional[str] = Security(_api_key_scheme),
    ) -> Optional[str]:
        secret = _resolve_secret()

        if not secret:
            # Authentication not configured — allow all requests in dev mode.
            logger.debug(
                "APIKeyAuth: no secret configured; permitting unauthenticated request."
            )
            return None

        if not api_key:
            if self._auto_error:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Missing X-API-Key header.",
                    headers={"WWW-Authenticate": "ApiKey"},
                )
            return None

        if not verify_api_key(api_key):
            if self._auto_error:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Invalid API key.",
                )
            return None

        return api_key


# ---------------------------------------------------------------------------
# JWT helpers (stdlib-only HS256 implementation)
# ---------------------------------------------------------------------------


def _jwt_sign(header_b64: str, payload_b64: str, secret: str) -> str:
    """Compute the HMAC-SHA256 signature segment of a JWT."""
    msg = f"{header_b64}.{payload_b64}".encode()
    raw = hmac.new(secret.encode(), msg, hashlib.sha256).digest()
    return _b64url_encode(raw)


def create_token(data: dict[str, Any], expires_minutes: int = 60) -> str:
    """Create a signed JWT containing *data*.

    Standard claims ``iat`` (issued-at) and ``exp`` (expiry) are added
    automatically.  The token is signed with HMAC-SHA256 using the secret
    returned by :func:`_resolve_secret`.

    Args:
        data:            Arbitrary payload dict; must be JSON-serialisable.
        expires_minutes: Token lifetime in minutes (default 60).

    Returns:
        A compact JWT string: ``<header_b64>.<payload_b64>.<signature_b64>``.
    """
    now = int(time.time())
    payload: dict[str, Any] = {
        **data,
        "iat": now,
        "exp": now + expires_minutes * 60,
    }

    header = {"alg": _JWT_ALGORITHM, "typ": "JWT"}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())

    # Fall back to a per-process random secret when nothing is configured so
    # tokens are at least self-consistent within a single process lifetime.
    secret = _resolve_secret() or secrets.token_hex(32)
    signature = _jwt_sign(header_b64, payload_b64, secret)

    return f"{header_b64}.{payload_b64}.{signature}"


def verify_token(token: str) -> Optional[dict[str, Any]]:
    """Verify *token* and return its payload dict, or ``None`` on failure.

    Checks performed:
    * Structural validity (three dot-separated segments).
    * HMAC-SHA256 signature integrity.
    * Token expiry (``exp`` claim).

    Args:
        token: The compact JWT string to verify.

    Returns:
        The decoded payload dict if valid, or ``None`` otherwise.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            logger.debug("verify_token: expected 3 JWT segments, got %d.", len(parts))
            return None

        header_b64, payload_b64, provided_sig = parts

        secret = _resolve_secret()
        if not secret:
            logger.warning("verify_token: no secret configured; cannot verify JWT.")
            return None

        expected_sig = _jwt_sign(header_b64, payload_b64, secret)
        if not hmac.compare_digest(provided_sig, expected_sig):
            logger.debug("verify_token: signature mismatch.")
            return None

        payload: dict[str, Any] = json.loads(_b64url_decode(payload_b64))

        exp = payload.get("exp")
        if exp is not None and int(time.time()) > int(exp):
            logger.debug("verify_token: token expired at %d.", exp)
            return None

        return payload

    except Exception as exc:
        logger.debug("verify_token: exception: %s", exc)
        return None


# ---------------------------------------------------------------------------
# FastAPI JWT dependency
# ---------------------------------------------------------------------------


class JWTAuth:
    """FastAPI dependency: validate a Bearer JWT from the ``Authorization`` header.

    Usage::

        from fastapi import Depends
        from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
        from cli.auth import JWTAuth

        bearer = HTTPBearer(auto_error=False)
        jwt_auth = JWTAuth()

        @app.get("/api/session")
        async def route(creds: HTTPAuthorizationCredentials = Depends(bearer),
                        payload: dict = Depends(jwt_auth)):
            return {"sub": payload.get("sub")}

    When initialised standalone (without injected credentials), pass the raw
    token string directly::

        payload = await jwt_auth(token="eyJ…")

    Args:
        auto_error: Raise 401 on failure when ``True`` (default);
                    return ``None`` when ``False``.
    """

    def __init__(self, auto_error: bool = True) -> None:
        self._auto_error = auto_error

    async def __call__(
        self,
        token: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        if not token:
            if self._auto_error:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Missing Bearer token.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return None

        payload = verify_token(token)
        if payload is None:
            if self._auto_error:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or expired JWT token.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return None

        return payload
