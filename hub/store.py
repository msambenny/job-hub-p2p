"""SQLite state: users, job posts, sent notifications, processed webhook ids."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    phone TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    subscribed INTEGER NOT NULL DEFAULT 0,
    profile_text TEXT NOT NULL DEFAULT '',
    prefs TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT '',
    state_data TEXT NOT NULL DEFAULT '{}',
    last_inbound_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    poster TEXT NOT NULL,
    status TEXT NOT NULL,
    record TEXT NOT NULL,
    validation TEXT NOT NULL DEFAULT '{}',
    reason TEXT NOT NULL DEFAULT '',
    path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
    seeker TEXT NOT NULL,
    job_id INTEGER NOT NULL,
    score REAL NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (seeker, job_id)
);
CREATE TABLE IF NOT EXISTS processed_messages (
    message_id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL
);
"""

# Job lifecycle.
NEEDS_LINK = "needs_link"
PENDING_REVIEW = "pending_review"
PUBLISHED = "published"
REJECTED = "rejected"
EXPIRED = "expired"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime | None = None) -> str:
    return (moment or utcnow()).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---- webhook idempotency -------------------------------------------------

    def mark_processed(self, message_id: str) -> bool:
        """Return False if this message id was already handled (WhatsApp retries)."""
        try:
            self.conn.execute(
                "INSERT INTO processed_messages (message_id, received_at) VALUES (?, ?)",
                (message_id, _iso()),
            )
        except sqlite3.IntegrityError:
            return False
        self.conn.commit()
        return True

    # ---- users ---------------------------------------------------------------

    def get_user(self, phone: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
        return _user(row) if row else None

    def touch_user(self, phone: str, name: str = "") -> dict[str, Any]:
        now = _iso()
        self.conn.execute(
            "INSERT INTO users (phone, name, last_inbound_at, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(phone) DO UPDATE SET last_inbound_at = excluded.last_inbound_at, "
            "name = CASE WHEN excluded.name != '' THEN excluded.name ELSE users.name END",
            (phone, name, now, now),
        )
        self.conn.commit()
        return self.get_user(phone)  # type: ignore[return-value]

    def update_user(self, phone: str, **fields: Any) -> None:
        if not fields:
            return
        for key in ("prefs", "state_data"):
            if key in fields:
                fields[key] = json.dumps(fields[key], ensure_ascii=False)
        if "subscribed" in fields:
            fields["subscribed"] = int(bool(fields["subscribed"]))
        assignments = ", ".join(f"{key} = ?" for key in fields)
        self.conn.execute(
            f"UPDATE users SET {assignments} WHERE phone = ?", (*fields.values(), phone)
        )
        self.conn.commit()

    def set_state(self, phone: str, state: str = "", data: dict[str, Any] | None = None) -> None:
        self.update_user(phone, state=state, state_data=data or {})

    def subscribers(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM users WHERE subscribed = 1 AND profile_text != ''"
        ).fetchall()
        return [_user(row) for row in rows]

    # ---- jobs ----------------------------------------------------------------

    def add_job(
        self,
        poster: str,
        record: dict[str, Any],
        status: str,
        *,
        validation: dict[str, Any] | None = None,
        reason: str = "",
    ) -> int:
        now = _iso()
        cursor = self.conn.execute(
            "INSERT INTO jobs (poster, status, record, validation, reason, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                poster,
                status,
                json.dumps(record, ensure_ascii=False),
                json.dumps(validation or {}, ensure_ascii=False),
                reason,
                now,
                now,
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def update_job(self, job_id: int, **fields: Any) -> None:
        for key in ("record", "validation"):
            if key in fields:
                fields[key] = json.dumps(fields[key], ensure_ascii=False)
        fields["updated_at"] = _iso()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        self.conn.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", (*fields.values(), job_id))
        self.conn.commit()

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _job(row) if row else None

    def jobs(self, *, status: str | None = None, poster: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM jobs WHERE 1 = 1"
        params: list[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if poster:
            query += " AND poster = ?"
            params.append(poster)
        rows = self.conn.execute(query + " ORDER BY id DESC", params).fetchall()
        return [_job(row) for row in rows]

    # ---- notifications -------------------------------------------------------

    def was_notified(self, seeker: str, job_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM notifications WHERE seeker = ? AND job_id = ?", (seeker, job_id)
        ).fetchone()
        return row is not None

    def record_notification(self, seeker: str, job_id: int, score: float) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO notifications (seeker, job_id, score, sent_at) VALUES (?, ?, ?, ?)",
            (seeker, job_id, score, _iso()),
        )
        self.conn.commit()

    def alerts_sent_since(self, seeker: str, hours: int = 24) -> int:
        since = _iso(utcnow() - timedelta(hours=hours))
        row = self.conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE seeker = ? AND sent_at >= ?", (seeker, since)
        ).fetchone()
        return int(row[0])


def _user(row: sqlite3.Row) -> dict[str, Any]:
    user = dict(row)
    user["prefs"] = json.loads(user["prefs"] or "{}")
    user["state_data"] = json.loads(user["state_data"] or "{}")
    user["subscribed"] = bool(user["subscribed"])
    return user


def _job(row: sqlite3.Row) -> dict[str, Any]:
    job = dict(row)
    job["record"] = json.loads(job["record"])
    job["validation"] = json.loads(job["validation"] or "{}")
    return job


def within_service_window(last_inbound_at: str, hours: int = 24) -> bool:
    """WhatsApp only allows free-form messages within 24h of the user's last message."""
    if not last_inbound_at:
        return False
    try:
        last = datetime.fromisoformat(last_inbound_at)
    except ValueError:
        return False
    return utcnow() - last < timedelta(hours=hours)
