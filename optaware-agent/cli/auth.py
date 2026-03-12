"""Authentication — API key and JWT token management."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from base64 import b64decode, b64encode
from pathlib import Path

logger = logging.getLogger("optaware.cli.auth")

API_KEYS_FILE = "/data/config/api_keys.json"


def generate_api_key() -> str:
    """Generate a new API key."""
    return f"oaw_{secrets.token_hex(32)}"


def _load_api_keys() -> set[str]:
    """Load stored API keys."""
    path = Path(API_KEYS_FILE)
    if path.exists():
        try:
            data = json.loads(path.read_text())
            return set(data.get("keys", []))
        except (json.JSONDecodeError, KeyError):
            pass
    return set()


def _save_api_keys(keys: set[str]) -> None:
    """Persist API keys."""
    path = Path(API_KEYS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"keys": list(keys)}))


def verify_api_key(key: str) -> bool:
    """Check if an API key is valid."""
    if not key:
        return False
    stored = _load_api_keys()
    # Also accept if no keys configured (first-run)
    if not stored:
        return True
    return key in stored


def register_api_key(key: str) -> None:
    """Register a new API key."""
    keys = _load_api_keys()
    keys.add(key)
    _save_api_keys(keys)


class JWTAuth:
    """Simple JWT-like token generation and verification."""

    def __init__(self, secret: str) -> None:
        self._secret = secret.encode()

    def create_token(self, data: dict, expires_minutes: int = 60) -> str:
        """Create a signed token."""
        payload = {
            **data,
            "exp": int(time.time()) + (expires_minutes * 60),
            "iat": int(time.time()),
        }
        payload_b64 = b64encode(json.dumps(payload).encode()).decode()
        signature = hmac.new(self._secret, payload_b64.encode(), hashlib.sha256).hexdigest()
        return f"{payload_b64}.{signature}"

    def verify_token(self, token: str) -> dict | None:
        """Verify and decode a token."""
        try:
            parts = token.split(".")
            if len(parts) != 2:
                return None

            payload_b64, signature = parts
            expected_sig = hmac.new(self._secret, payload_b64.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected_sig):
                return None

            payload = json.loads(b64decode(payload_b64))

            if payload.get("exp", 0) < time.time():
                return None

            return payload
        except (json.JSONDecodeError, ValueError, KeyError):
            return None
