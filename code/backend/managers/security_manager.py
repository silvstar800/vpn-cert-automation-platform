"""Security and authentication management."""

import base64
import hashlib
import hmac
import json
import secrets
import threading
from datetime import datetime, timezone


class RateLimitExceeded(Exception):
    """Raised when the login rate limit has been exceeded."""


class SecurityManager:
    """Manages security, authentication, and session operations."""

    def __init__(self, secret: str):
        """Initialize SecurityManager with secret key."""
        self.secret = secret
        self.login_attempts: dict[str, list[float]] = {}
        self.login_attempts_lock = threading.Lock()

    def sign(self, msg: str) -> str:
        """Generate HMAC signature for message."""
        return hmac.new(
            self.secret.encode("utf-8"),
            msg.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def verify_signature(self, msg: str, signature: str) -> bool:
        """Verify HMAC signature."""
        expected = self.sign(msg)
        return hmac.compare_digest(expected, signature)

    def verify_pbkdf2_password(self, password: str, encoded: str) -> bool:
        """Verify PBKDF2 hashed password."""
        if not encoded or not encoded.startswith("pbkdf2_sha256$"):
            return False

        try:
            algorithm, iterations_text, salt, stored_hash = encoded.split("$", 3)
            if algorithm != "pbkdf2_sha256":
                return False
            iterations = int(iterations_text)
            computed_hash = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt.encode("utf-8"),
                iterations,
            ).hex()
            return hmac.compare_digest(computed_hash, stored_hash)
        except Exception:
            return False

    def build_session_cookie(self, username: str, ttl_seconds: int = 28800) -> str:
        """Build a signed session cookie with an explicit expiry."""
        now_ts = int(datetime.now(tz=timezone.utc).timestamp())
        payload = {
            "sub": username,
            "iat": now_ts,
            "exp": now_ts + int(ttl_seconds),
            "nonce": secrets.token_hex(16),
        }
        payload_json = json.dumps(payload, separators=(",", ":"))
        payload_b64 = base64.urlsafe_b64encode(payload_json.encode("utf-8")).decode("ascii")
        signature = self.sign(payload_b64)
        return f"{payload_b64}.{signature}"

    def parse_session_cookie(self, cookie_value: str | None) -> dict | None:
        """Parse, verify, and validate a signed session cookie."""
        if not cookie_value or "." not in cookie_value:
            return None

        payload_b64, signature = cookie_value.rsplit(".", 1)
        if not self.verify_signature(payload_b64, signature):
            return None

        try:
            payload_json = base64.urlsafe_b64decode(payload_b64.encode("ascii")).decode("utf-8")
            payload = json.loads(payload_json)
        except Exception:
            return None

        if not isinstance(payload, dict):
            return None

        if "sub" not in payload and payload.get("username"):
            payload["sub"] = payload.get("username")

        try:
            exp = int(payload.get("exp", 0))
        except (TypeError, ValueError):
            return None

        if exp and exp < int(datetime.now(tz=timezone.utc).timestamp()):
            return None
        return payload

    def clear_expired_login_attempts(self, source_ip: str, window_seconds: int = 600) -> list[float]:
        """Prune expired failed login attempts and return the recent window."""
        if not source_ip:
            return []
        now_ts = datetime.now(tz=timezone.utc).timestamp()
        cutoff = now_ts - float(window_seconds)

        with self.login_attempts_lock:
            attempts = [ts for ts in self.login_attempts.get(source_ip, []) if ts > cutoff]
            if attempts:
                self.login_attempts[source_ip] = attempts
            else:
                self.login_attempts.pop(source_ip, None)
            return attempts

    def check_login_rate_limit(self, source_ip: str, max_attempts: int = 5, window_seconds: int = 600) -> bool:
        """Raise when the IP has exceeded the allowed failed login count."""
        attempts = self.clear_expired_login_attempts(source_ip, window_seconds=window_seconds)
        if len(attempts) >= int(max_attempts):
            raise RateLimitExceeded("too many login attempts")
        return True

    def register_login_failure(self, source_ip: str) -> None:
        """Record a failed login attempt."""
        if not source_ip:
            return
        now_ts = datetime.now(tz=timezone.utc).timestamp()
        with self.login_attempts_lock:
            self.login_attempts.setdefault(source_ip, []).append(now_ts)

    def clear_login_attempts(self, source_ip: str) -> None:
        """Clear login failures after a successful login."""
        if not source_ip:
            return
        with self.login_attempts_lock:
            self.login_attempts.pop(source_ip, None)

    def get_login_failure_count(self, source_ip: str, window_seconds: int = 600) -> int:
        """Get the current failed-login count for the IP."""
        return len(self.clear_expired_login_attempts(source_ip, window_seconds=window_seconds))
