"""
Append-only security audit trail (S1; S2/S3/S4 call `log`).

Events go to the `security_events` table in data/security_audit.db. SQLite
triggers abort UPDATE and DELETE so rows cannot be altered through the
application's connection. `log` never raises: auditing must not take the
request path down; failures are logged to the normal logger instead.
"""

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger("drdo-rag")

DB_PATH = Path(__file__).parent / "data" / "security_audit.db"


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS security_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            event TEXT NOT NULL,
            user_id TEXT,
            tenant_id TEXT,
            ip TEXT,
            detail TEXT
        )"""
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS security_events_no_update BEFORE UPDATE ON security_events "
        "BEGIN SELECT RAISE(ABORT, 'security_events is append-only'); END"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS security_events_no_delete BEFORE DELETE ON security_events "
        "BEGIN SELECT RAISE(ABORT, 'security_events is append-only'); END"
    )
    return conn


def log(
    event: str,
    user_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    ip: Optional[str] = None,
    detail: Any = None,
) -> None:
    """Record one security event. Never put secrets/tokens/passwords in `detail`."""
    try:
        if detail is not None and not isinstance(detail, str):
            detail = json.dumps(detail, default=str)[:2000]
        elif isinstance(detail, str):
            detail = detail[:2000]
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO security_events (ts, event, user_id, tenant_id, ip, detail) VALUES (?,?,?,?,?,?)",
                (time.time(), event, user_id, tenant_id, ip, detail),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - auditing must never break callers
        logger.error(f"[security_audit] failed to record '{event}': {e}")


def recent(limit: int = 100, event: Optional[str] = None) -> List[dict]:
    conn = _conn()
    try:
        if event:
            rows = conn.execute(
                "SELECT * FROM security_events WHERE event = ? ORDER BY id DESC LIMIT ?", (event, limit)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM security_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]
