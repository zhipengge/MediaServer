"""Optional token authentication + login rate limiting.

The NAS-DEPLOY contract assumes a trusted LAN with no authentication.  That is
the default (``MEDIA_AUTH_MODE=off``).  The moment the server is reachable from
the internet it MUST run with ``MEDIA_AUTH_MODE=token``; the reverse-proxy guide
in ``docs/REMOTE-ACCESS.md`` explains the TLS/basic-auth layer on top.
"""

from __future__ import annotations

import hmac
import os
import secrets
import threading
import time
from pathlib import Path

from flask import request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

PUBLIC_PATHS = {"/api/v1/status", "/healthz", "/login", "/favicon.ico"}
PUBLIC_PREFIXES = ("/static/",)

_MAX_ATTEMPTS = 8
_WINDOW_SECONDS = 300
QR_LOGIN_TTL = 120  # seconds a scan-to-login QR stays valid


class AuthService:
    def __init__(self, config, secret_file: Path):
        self.config = config
        self.enabled = config.auth_enabled
        self._token = config.auth_token.encode("utf-8")
        self.secret_key = config.secret_key or self._load_or_create_secret(secret_file)
        self._attempts: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._login_serializer = URLSafeTimedSerializer(
            self.secret_key, salt="mediaserver-qr-login")
        self._used_login_tokens: dict[str, float] = {}

    @staticmethod
    def _load_or_create_secret(path: Path) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if path.is_file():
                value = path.read_text("utf-8").strip()
                if len(value) >= 32:
                    return value
            value = secrets.token_hex(32)
            path.write_text(value, "utf-8")
            os.chmod(path, 0o600)
            return value
        except OSError:
            # Ephemeral fallback: sessions die with the process, but never fail
            # to boot because DATA_DIR is read-only.
            return secrets.token_hex(32)

    def matches(self, candidate: str | None) -> bool:
        if not self.enabled or not candidate:
            return False
        return hmac.compare_digest(candidate.encode("utf-8"), self._token)

    def is_public(self, path: str) -> bool:
        return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)

    def authorized(self, req) -> bool:
        if not self.enabled:
            return True
        if self.is_public(req.path):
            return True
        # Web UI: signed session cookie.
        try:
            from flask import session

            if session.get("auth") is True:
                return True
        except Exception:
            pass
        # API clients: Authorization: Bearer <token> or X-API-Token.
        header = req.headers.get("Authorization", "")
        if header.lower().startswith("bearer ") and self.matches(header[7:].strip()):
            return True
        return self.matches(req.headers.get("X-API-Token"))

    # ── scan-to-login one-time tokens ───────────────────────────────
    def issue_login_token(self) -> str:
        """Signed token for `/login?ot=`; valid for QR_LOGIN_TTL seconds."""
        return self._login_serializer.dumps({"v": 1})

    def consume_login_token(self, token: str | None) -> bool:
        """Verify and burn a login token (single use per process)."""
        if not token:
            return False
        try:
            payload = self._login_serializer.loads(token, max_age=QR_LOGIN_TTL)
        except (BadSignature, SignatureExpired):
            return False
        if not isinstance(payload, dict) or payload.get("v") != 1:
            return False
        now = time.time()
        with self._lock:
            self._used_login_tokens = {
                key: expiry for key, expiry in self._used_login_tokens.items()
                if expiry > now
            }
            if token in self._used_login_tokens:
                return False
            self._used_login_tokens[token] = now + QR_LOGIN_TTL
        return True

    # ── login throttling ────────────────────────────────────────────
    def _client(self) -> str:
        if self.config.trust_proxy:
            forwarded = request.headers.get("X-Real-IP") or request.headers.get("X-Forwarded-For")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return request.remote_addr or "unknown"

    def throttle_hit(self) -> bool:
        """Record a failed attempt; returns True when the client is blocked."""
        client = self._client()
        now = time.time()
        with self._lock:
            attempts = [t for t in self._attempts.get(client, []) if now - t < _WINDOW_SECONDS]
            attempts.append(now)
            self._attempts[client] = attempts
            return len(attempts) > _MAX_ATTEMPTS

    def throttled(self) -> bool:
        client = self._client()
        now = time.time()
        with self._lock:
            attempts = [t for t in self._attempts.get(client, []) if now - t < _WINDOW_SECONDS]
            self._attempts[client] = attempts
            return len(attempts) > _MAX_ATTEMPTS

    def clear(self) -> None:
        with self._lock:
            self._attempts.pop(self._client(), None)
