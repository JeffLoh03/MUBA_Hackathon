"""Password authentication and revocable, server-side sessions for Verity Desk."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from backend.database import AuditStore, utc_database_timestamp


SESSION_COOKIE = "verity_session"
SESSION_SECONDS = 12 * 60 * 60
PASSWORD_ITERATIONS = 600_000
_DUMMY_HASH = f"pbkdf2_sha256${PASSWORD_ITERATIONS}${'0' * 32}${'0' * 64}"


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def normalized_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address.")
        return value


@dataclass(frozen=True)
class User:
    id: str
    email: str

    def public(self) -> dict[str, str]:
        return {"id": self.id, "email": self.email}


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${derived.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt, expected = stored.split("$")
        if algorithm != "pbkdf2_sha256" or int(rounds) != PASSWORD_ITERATIONS:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def is_direct_loopback(request: Request) -> bool:
    if request.client is None:
        return False
    if any(name in request.headers for name in ("forwarded", "x-forwarded-for", "x-real-ip")):
        return False
    # Also reject a hostile DNS name rebinding to loopback in the user's browser.
    hostname = request.url.hostname or ""
    if hostname.lower() != "localhost":
        try:
            if not ipaddress.ip_address(hostname).is_loopback:
                return False
        except ValueError:
            return False
    try:
        return ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        return False


def _origin_tuple(value: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return None
        return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None


def require_same_origin(request: Request) -> None:
    """Reject browser CSRF, including login CSRF, before changing any state."""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        raise HTTPException(status_code=403, detail="Cross-site requests are not allowed.")
    origin = request.headers.get("origin")
    if origin is None:
        # Non-browser clients need no CSRF token; browsers send Origin on fetch.
        return
    secure = secure_cookie_enabled()
    # Honor explicit HTTPS deployment configuration, never forwarding headers.
    expected = _origin_tuple(f"{'https' if secure else request.url.scheme}://{request.headers.get('host', '')}")
    if expected is None or _origin_tuple(origin) != expected:
        raise HTTPException(status_code=403, detail="Cross-site requests are not allowed.")


def secure_cookie_enabled() -> bool:
    return os.getenv("VERITY_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes"}


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
        secure=secure_cookie_enabled(), samesite="strict", path="/",
    )


class AuthStore:
    def __init__(self, store: AuditStore) -> None:
        self.store = store

    def setup_required(self) -> bool:
        with self.store._connect() as connection:
            return connection.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None

    def create_first_user(self, credentials: Credentials) -> User:
        password_hash = hash_password(credentials.password)
        user = User(uuid4().hex, credentials.email)
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None:
                raise HTTPException(status_code=409, detail="Setup is already complete. Sign in to continue.")
            connection.execute(
                "INSERT INTO users (id, email, password_hash, created_at_utc) VALUES (?, ?, ?, ?)",
                (user.id, user.email, password_hash, utc_database_timestamp()),
            )
            connection.execute("UPDATE verification_runs SET owner_user_id = ? WHERE owner_user_id IS NULL", (user.id,))
        return user

    def authenticate(self, credentials: Credentials) -> User | None:
        with self.store._connect() as connection:
            row = connection.execute("SELECT id, email, password_hash FROM users WHERE email = ?", (credentials.email,)).fetchone()
        # Unknown accounts still pay the password hashing cost.
        valid = check_password(credentials.password, row["password_hash"] if row else _DUMMY_HASH)
        return User(row["id"], row["email"]) if row and valid else None

    def create_session(self, user: User, previous_token: str | None = None) -> str:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = int(time.time())
        with self.store._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            if previous_token:
                connection.execute("DELETE FROM sessions WHERE token_hash = ?", (hashlib.sha256(previous_token.encode()).hexdigest(),))
            connection.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)", (token_hash, user.id, now + SESSION_SECONDS))
        return token

    def session_user(self, token: str | None) -> User | None:
        if not token or len(token) > 128:
            return None
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT u.id, u.email FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires_at > ?",
                (hashlib.sha256(token.encode()).hexdigest(), int(time.time())),
            ).fetchone()
        return User(row["id"], row["email"]) if row else None

    def revoke_session(self, token: str | None) -> None:
        if token:
            with self.store._connect() as connection:
                connection.execute("DELETE FROM sessions WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))

    def consume_rate_limit(self, bucket: str, *, limit: int, window_seconds: int) -> None:
        now = int(time.time())
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM rate_limit_hits WHERE occurred_at <= ?", (now - 3600,))
            count = connection.execute(
                "SELECT COUNT(*) FROM rate_limit_hits WHERE bucket = ? AND occurred_at > ?", (bucket, now - window_seconds)
            ).fetchone()[0]
            if count >= limit:
                raise HTTPException(status_code=429, detail="Too many attempts. Try again later.", headers={"Retry-After": str(window_seconds)})
            connection.execute("INSERT INTO rate_limit_hits (bucket, occurred_at) VALUES (?, ?)", (bucket, now))
