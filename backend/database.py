from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any, Iterator
from uuid import uuid4

from dotenv import load_dotenv

from backend.schemas.models import FactCheckReport


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "verity_desk.db"


def utc_database_timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def configured_database_path() -> Path:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    configured = os.getenv("VERITY_DB_PATH", "").strip()
    if not configured:
        return DEFAULT_DATABASE_PATH
    path = Path(configured)
    return path if path.is_absolute() else PROJECT_ROOT / path


class AuditStore:
    """SQLite persistence for account-owned verification records and Gonka trace IDs."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else configured_database_path()
        self._initialization_lock = RLock()
        self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._initialization_lock:
            if self._initialized:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect(initialize=False) as connection:
                connection.executescript(
                    """
                    PRAGMA journal_mode = WAL;
                    PRAGMA foreign_keys = ON;

                    CREATE TABLE IF NOT EXISTS verification_runs (
                        id TEXT PRIMARY KEY,
                        owner_user_id TEXT,
                        input_type TEXT NOT NULL,
                        input_text TEXT NOT NULL DEFAULT '',
                        article_url TEXT NOT NULL DEFAULT '',
                        image_name TEXT NOT NULL DEFAULT '',
                        mode TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at_utc TEXT NOT NULL,
                        completed_at_utc TEXT,
                        extracted_claim TEXT NOT NULL DEFAULT '',
                        final_verdict TEXT,
                        truth_score INTEGER,
                        confidence_score INTEGER,
                        report_json TEXT,
                        error_message TEXT
                    );

                    CREATE TABLE IF NOT EXISTS audit_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        sequence_number INTEGER NOT NULL,
                        stage TEXT NOT NULL,
                        timestamp_utc TEXT NOT NULL,
                        details_json TEXT NOT NULL DEFAULT '{}',
                        FOREIGN KEY (run_id) REFERENCES verification_runs(id) ON DELETE CASCADE,
                        UNIQUE (run_id, sequence_number)
                    );

                    CREATE TABLE IF NOT EXISTS gonka_calls (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        sequence_number INTEGER NOT NULL,
                        step_name TEXT NOT NULL,
                        requested_model_id TEXT NOT NULL,
                        returned_model_id TEXT,
                        response_body_id TEXT,
                        request_id TEXT,
                        trace_id TEXT,
                        timestamp_utc TEXT NOT NULL,
                        latency_ms REAL NOT NULL,
                        token_usage_json TEXT,
                        success INTEGER NOT NULL,
                        error_type TEXT,
                        safe_error_message TEXT,
                        FOREIGN KEY (run_id) REFERENCES verification_runs(id) ON DELETE CASCADE,
                        UNIQUE (run_id, sequence_number)
                    );

                    CREATE INDEX IF NOT EXISTS idx_verification_runs_created
                    ON verification_runs(created_at_utc DESC);
                    CREATE INDEX IF NOT EXISTS idx_audit_events_run
                    ON audit_events(run_id, sequence_number);
                    CREATE INDEX IF NOT EXISTS idx_gonka_calls_run
                    ON gonka_calls(run_id, sequence_number);
                    """
                )
                # Upgrade existing local ledgers without losing their reports.
                columns = {row["name"] for row in connection.execute("PRAGMA table_info(verification_runs)")}
                if "owner_user_id" not in columns:
                    connection.execute("ALTER TABLE verification_runs ADD COLUMN owner_user_id TEXT")
                trace_columns = {row["name"] for row in connection.execute("PRAGMA table_info(gonka_calls)")}
                if "claim_index" not in trace_columns:
                    connection.execute("ALTER TABLE gonka_calls ADD COLUMN claim_index INTEGER")
                if "claim" not in trace_columns:
                    connection.execute("ALTER TABLE gonka_calls ADD COLUMN claim TEXT")
                connection.executescript(
                    """
                    CREATE INDEX IF NOT EXISTS idx_verification_runs_owner
                    ON verification_runs(owner_user_id, created_at_utc DESC);
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                        password_hash TEXT NOT NULL,
                        created_at_utc TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS sessions (
                        token_hash TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        expires_at INTEGER NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
                    CREATE TABLE IF NOT EXISTS rate_limit_hits (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        bucket TEXT NOT NULL,
                        occurred_at INTEGER NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_rate_limit_hits_bucket
                    ON rate_limit_hits(bucket, occurred_at);
                    PRAGMA user_version = 2;
                    """
                )
            self._initialized = True

    def create_run(
        self,
        *,
        input_type: str,
        input_text: str,
        article_url: str,
        image_name: str,
        mode: str,
        owner_user_id: str | None = None,
    ) -> str:
        run_id = uuid4().hex
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO verification_runs (
                    id, input_type, input_text, article_url, image_name,
                    mode, status, created_at_utc, owner_user_id
                ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?)
                """,
                (
                    run_id,
                    input_type,
                    input_text,
                    article_url,
                    image_name,
                    mode,
                    utc_database_timestamp(),
                    owner_user_id,
                ),
            )
        return run_id

    def append_event(
        self,
        run_id: str,
        *,
        sequence_number: int,
        stage: str,
        timestamp_utc: str,
        details: dict[str, Any],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_events (
                    run_id, sequence_number, stage, timestamp_utc, details_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    sequence_number,
                    stage,
                    timestamp_utc,
                    json.dumps(details, ensure_ascii=True, default=str),
                ),
            )

    def complete_run(self, run_id: str, report: FactCheckReport, completed_at_utc: str) -> None:
        report_data = report.model_dump(mode="json")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE verification_runs
                SET status = 'completed', completed_at_utc = ?, extracted_claim = ?,
                    final_verdict = ?, truth_score = ?, confidence_score = ?,
                    report_json = ?, error_message = NULL
                WHERE id = ?
                """,
                (
                    completed_at_utc,
                    report.extracted_claim,
                    report.final_verdict,
                    report.truth_score,
                    report.confidence_score,
                    json.dumps(report_data, ensure_ascii=True),
                    run_id,
                ),
            )
            connection.execute("DELETE FROM gonka_calls WHERE run_id = ?", (run_id,))
            connection.executemany(
                """
                INSERT INTO gonka_calls (
                    run_id, sequence_number, step_name, requested_model_id,
                    returned_model_id, response_body_id, request_id, trace_id,
                    timestamp_utc, latency_ms, token_usage_json, success,
                    error_type, safe_error_message, claim_index, claim
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        index,
                        trace.step_name,
                        trace.requested_model_id,
                        trace.returned_model_id,
                        trace.response_body_id,
                        trace.request_id,
                        trace.trace_id,
                        trace.timestamp_utc,
                        trace.latency_ms,
                        json.dumps(trace.token_usage, ensure_ascii=True) if trace.token_usage else None,
                        int(trace.success),
                        trace.error_type,
                        trace.safe_error_message,
                        getattr(trace, "claim_index", None),
                        getattr(trace, "claim", None),
                    )
                    for index, trace in enumerate(report.gonka_trace, start=1)
                ],
            )

    def fail_run(self, run_id: str, message: str, completed_at_utc: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE verification_runs
                SET status = 'failed', completed_at_utc = ?, error_message = ?
                WHERE id = ?
                """,
                (completed_at_utc, message[:1000], run_id),
            )

    def list_runs(self, *, limit: int = 50, owner_user_id: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT r.id, r.input_type, r.input_text, r.article_url, r.image_name,
                       r.mode, r.status, r.created_at_utc, r.completed_at_utc,
                       r.extracted_claim, r.final_verdict, r.truth_score,
                       r.confidence_score, r.error_message,
                       COUNT(c.id) AS gonka_call_count
                FROM verification_runs AS r
                LEFT JOIN gonka_calls AS c ON c.run_id = r.id
                WHERE (? IS NULL OR r.owner_user_id = ?)
                GROUP BY r.id
                ORDER BY r.created_at_utc DESC
                LIMIT ?
                """,
                (owner_user_id, owner_user_id, max(1, min(limit, 100))),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_run(self, run_id: str, *, owner_user_id: str | None = None) -> dict[str, Any] | None:
        with self._connect() as connection:
            run = connection.execute(
                "SELECT * FROM verification_runs WHERE id = ? AND (? IS NULL OR owner_user_id = ?)",
                (run_id, owner_user_id, owner_user_id),
            ).fetchone()
            if run is None:
                return None
            events = connection.execute(
                """
                SELECT sequence_number, stage, timestamp_utc, details_json
                FROM audit_events WHERE run_id = ? ORDER BY sequence_number
                """,
                (run_id,),
            ).fetchall()
            calls = connection.execute(
                """
                SELECT sequence_number, step_name, requested_model_id,
                       returned_model_id, response_body_id, request_id, trace_id,
                       timestamp_utc, latency_ms, token_usage_json, success,
                       error_type, safe_error_message, claim_index, claim
                FROM gonka_calls WHERE run_id = ? ORDER BY sequence_number
                """,
                (run_id,),
            ).fetchall()

        result = dict(run)
        report_json = result.pop("report_json")
        result["report"] = json.loads(report_json) if report_json else None
        result["events"] = [
            {
                **{key: row[key] for key in ("sequence_number", "stage", "timestamp_utc")},
                "details": json.loads(row["details_json"]),
            }
            for row in events
        ]
        result["gonka_calls"] = [
            {
                **dict(row),
                "success": bool(row["success"]),
                "token_usage": json.loads(row["token_usage_json"]) if row["token_usage_json"] else None,
            }
            for row in calls
        ]
        for call in result["gonka_calls"]:
            call.pop("token_usage_json", None)
        return result

    @contextmanager
    def _connect(self, *, initialize: bool = True) -> Iterator[sqlite3.Connection]:
        if initialize:
            self.initialize()
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()
