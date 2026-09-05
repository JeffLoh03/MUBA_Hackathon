from __future__ import annotations

import hashlib
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import api
from backend.auth import AuthStore, Credentials, SESSION_COOKIE, User, check_password, hash_password
from backend.database import AuditStore


ACCOUNT = {"email": "owner@example.com", "password": "correct-horse-battery-staple"}


@pytest.fixture
def store(monkeypatch, tmp_path):
    store = AuditStore(tmp_path / "auth.db")
    monkeypatch.setattr(api, "audit_store", store)
    monkeypatch.delenv("VERITY_COOKIE_SECURE", raising=False)
    return store


@pytest.fixture
def client(store):
    return TestClient(api.app, client=("127.0.0.1", 50000), base_url="http://127.0.0.1")


def test_setup_login_logout_revokes_session_and_passwords_are_hashed(client, store):
    assert client.get("/api/auth/status").json() == {"authenticated": False, "setup_required": True, "user": None}
    setup = client.post("/api/auth/setup", json=ACCOUNT)
    assert setup.status_code == 200
    assert setup.json()["user"]["email"] == ACCOUNT["email"]
    assert setup.json()["authenticated"] is True
    assert setup.json()["setup_required"] is False
    cookie = setup.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "max-age=43200" in cookie
    token = client.cookies.get(SESSION_COOKIE)
    with store._connect() as connection:
        password_hash = connection.execute("SELECT password_hash FROM users").fetchone()[0]
        session_hash = connection.execute("SELECT token_hash FROM sessions").fetchone()[0]
    assert ACCOUNT["password"] not in password_hash
    assert check_password(ACCOUNT["password"], password_hash)
    assert session_hash == hashlib.sha256(token.encode()).hexdigest()
    assert client.get("/api/audits").status_code == 200
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/audits").status_code == 401
    assert api.auth_store().session_user(token) is None
    assert client.post("/api/auth/login", json=ACCOUNT).status_code == 200
    assert client.cookies.get(SESSION_COOKIE) != token


@pytest.mark.parametrize("endpoint,method", [
    ("/api/audits", "GET"),
    ("/api/audits/" + "0" * 32, "GET"),
    ("/api/verify/stream", "POST"),
    ("/api/verify/image/stream", "POST"),
])
def test_verification_and_audits_require_login(client, endpoint, method):
    assert client.request(method, endpoint, json={"text": "A claim"}).status_code == 401


def test_setup_only_once_and_only_direct_local_client(client, store):
    remote = TestClient(api.app, client=("203.0.113.8", 50000))
    assert remote.post("/api/auth/setup", json=ACCOUNT).status_code == 403
    assert client.post("/api/auth/setup", json=ACCOUNT, headers={"X-Forwarded-For": "203.0.113.8"}).status_code == 403
    assert client.post("/api/auth/setup", json=ACCOUNT).status_code == 200
    assert client.post("/api/auth/setup", json={**ACCOUNT, "email": "takeover@example.com"}).status_code == 409


def test_simultaneous_setup_creates_exactly_one_account(store):
    def create_first(index):
        try:
            AuthStore(AuditStore(store.path)).create_first_user(Credentials(email=f"user{index}@example.com", password=ACCOUNT["password"]))
            return 200
        except HTTPException as exc:
            return exc.status_code

    store.initialize()
    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(create_first, [1, 2]))
    assert sorted(statuses) == [200, 409]


def test_wrong_credentials_generic_errors_and_attempt_limit(client):
    assert client.post("/api/auth/setup", json=ACCOUNT).status_code == 200
    client.post("/api/auth/logout")
    wrong = {**ACCOUNT, "password": "this-password-is-incorrect"}
    response = client.post("/api/auth/login", json=wrong)
    missing = client.post("/api/auth/login", json={**wrong, "email": "unknown@example.com"})
    assert response.status_code == missing.status_code == 401
    assert response.json() == missing.json() == {"detail": "Email or password is incorrect."}
    for _ in range(7):
        assert client.post("/api/auth/login", json=wrong).status_code == 401
    assert client.post("/api/auth/login", json=ACCOUNT).status_code == 429


def test_cross_origin_setup_login_and_logout_are_rejected(client):
    assert client.post("/api/auth/setup", json=ACCOUNT, headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.post("/api/auth/setup", json=ACCOUNT, headers={"Origin": "http://127.0.0.1"}).status_code == 200
    assert client.post("/api/auth/logout", headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.post("/api/auth/login", json=ACCOUNT, headers={"Origin": "null"}).status_code == 403
    assert client.post("/api/auth/logout", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/api/auth/status").json()["authenticated"] is True


def test_vite_proxy_origin_is_accepted_when_host_is_preserved(store):
    client = TestClient(api.app, client=("127.0.0.1", 50000), base_url="http://localhost:5173")
    assert client.post("/api/auth/setup", json=ACCOUNT, headers={"Origin": "http://localhost:5173"}).status_code == 200


def test_secure_deployment_cookie_and_expiry(client, store, monkeypatch):
    monkeypatch.setenv("VERITY_COOKIE_SECURE", "true")
    response = client.post("/api/auth/setup", json=ACCOUNT)
    assert "Secure" in response.headers["set-cookie"]
    token = client.cookies.get(SESSION_COOKIE)
    assert api.auth_store().session_user(token) is not None
    with store._connect() as connection:
        connection.execute("UPDATE sessions SET expires_at = ?", (int(time.time()) - 1,))
    assert api.auth_store().session_user(token) is None


def test_audits_are_owned_and_legacy_records_move_to_first_account(client, store):
    legacy = store.create_run(input_type="text", input_text="Legacy", article_url="", image_name="", mode="quick")
    setup = client.post("/api/auth/setup", json=ACCOUNT)
    owner_id = setup.json()["user"]["id"]
    assert store.get_run(legacy)["owner_user_id"] == owner_id
    other = User("b" * 32, "other@example.com")
    with store._connect() as connection:
        connection.execute("INSERT INTO users (id, email, password_hash, created_at_utc) VALUES (?, ?, ?, ?)", (other.id, other.email, hash_password(ACCOUNT["password"]), "2026-09-05T00:00:00Z"))
    other_run = store.create_run(input_type="text", input_text="Private", article_url="", image_name="", mode="quick", owner_user_id=other.id)
    assert [row["id"] for row in client.get("/api/audits").json()["runs"]] == [legacy]
    assert client.get(f"/api/audits/{other_run}").status_code == 404
    other_client = TestClient(api.app)
    other_client.cookies.set(SESSION_COOKIE, api.auth_store().create_session(other))
    assert [row["id"] for row in other_client.get("/api/audits").json()["runs"]] == [other_run]
    assert other_client.get(f"/api/audits/{legacy}").status_code == 404


def test_old_database_schema_migrates_without_losing_report(tmp_path):
    database_path = tmp_path / "legacy.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript("""
            CREATE TABLE verification_runs (
                id TEXT PRIMARY KEY, input_type TEXT NOT NULL, input_text TEXT NOT NULL DEFAULT '',
                article_url TEXT NOT NULL DEFAULT '', image_name TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL,
                status TEXT NOT NULL, created_at_utc TEXT NOT NULL, completed_at_utc TEXT, extracted_claim TEXT NOT NULL DEFAULT '',
                final_verdict TEXT, truth_score INTEGER, confidence_score INTEGER, report_json TEXT, error_message TEXT
            );
            INSERT INTO verification_runs(id, input_type, input_text, mode, status, created_at_utc)
            VALUES ('legacy', 'text', 'Original claim', 'quick', 'failed', '2026-09-04T00:00:00Z');
            PRAGMA user_version = 1;
        """)
    store = AuditStore(database_path)
    user = AuthStore(store).create_first_user(Credentials(**ACCOUNT))
    record = store.get_run("legacy", owner_user_id=user.id)
    assert record["input_text"] == "Original claim"
    assert record["owner_user_id"] == user.id


def test_verification_rate_limit_and_remote_browser_guard(client, store):
    client.post("/api/auth/setup", json=ACCOUNT)
    user = api.auth_store().session_user(client.cookies.get(SESSION_COOKIE))
    for _ in range(20):
        api.auth_store().consume_rate_limit(f"verify:{user.id}", limit=20, window_seconds=3600)
    assert client.post("/api/verify/stream", json={"text": "Claim"}).status_code == 429
    with store._connect() as connection:
        connection.execute("DELETE FROM rate_limit_hits")
    remote = TestClient(api.app, client=("203.0.113.8", 50000))
    remote.cookies.set(SESSION_COOKIE, api.auth_store().create_session(user))
    assert remote.post("/api/verify/stream", json={"text": "Claim", "show_browser": True}).status_code == 403


@pytest.mark.parametrize("password", ["short", "x" * 129])
def test_setup_enforces_password_length(client, password):
    assert client.post("/api/auth/setup", json={**ACCOUNT, "password": password}).status_code == 422


def test_setup_rejects_rebound_host_and_api_is_not_cacheable(client):
    response = client.post("/api/auth/setup", json=ACCOUNT, headers={"Host": "attacker.example", "Origin": "http://attacker.example"})
    assert response.status_code == 403
    assert client.get("/api/auth/status").headers["cache-control"] == "no-store"
