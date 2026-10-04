"""
Model-call telemetry — every AIGateway.complete() call is logged here.

Follows the same local-SQLite pattern already used by
VENUAI/guardrails.py's audit log and VENUAI/auth.py's user store, rather
than introducing a new storage technology for one new table.

Never logs message content (could contain uploaded-document text or user
queries) — only shape and outcome, per spec section 37 ("never log raw
secrets/content unnecessarily").
"""

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "model_routing_log.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS model_routing_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       REAL NOT NULL,
                task_type       TEXT NOT NULL,
                requested_chain TEXT NOT NULL,
                model_used      TEXT,
                provider        TEXT,
                fallback_count  INTEGER NOT NULL DEFAULT 0,
                latency_ms      REAL,
                input_tokens    INTEGER,
                output_tokens   INTEGER,
                success         INTEGER NOT NULL,
                error           TEXT
            )
            """
        )
        conn.commit()


init_db()


@dataclass
class RoutingLogEntry:
    task_type: str
    requested_chain: str          # comma-joined model ids that were tried, in order
    model_used: Optional[str]
    provider: Optional[str]
    fallback_count: int
    latency_ms: Optional[float]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    success: bool
    error: Optional[str] = None


def record(entry: RoutingLogEntry) -> None:
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO model_routing_log
                (timestamp, task_type, requested_chain, model_used, provider,
                 fallback_count, latency_ms, input_tokens, output_tokens, success, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(), entry.task_type, entry.requested_chain, entry.model_used,
                entry.provider, entry.fallback_count, entry.latency_ms,
                entry.input_tokens, entry.output_tokens, int(entry.success), entry.error,
            ),
        )
        conn.commit()


def recent(limit: int = 100) -> list:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM model_routing_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def summary() -> dict:
    """Aggregate stats for the admin dashboard / observability endpoint."""
    with _get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM model_routing_log").fetchone()["c"]
        if total == 0:
            return {"total_calls": 0, "success_rate": None, "avg_latency_ms": None, "fallback_rate": None}
        succ = conn.execute("SELECT COUNT(*) AS c FROM model_routing_log WHERE success = 1").fetchone()["c"]
        avg_latency = conn.execute("SELECT AVG(latency_ms) AS a FROM model_routing_log WHERE success = 1").fetchone()["a"]
        fallbacks = conn.execute("SELECT COUNT(*) AS c FROM model_routing_log WHERE fallback_count > 0").fetchone()["c"]
        return {
            "total_calls": total,
            "success_rate": round(succ / total, 3),
            "avg_latency_ms": round(avg_latency, 1) if avg_latency else None,
            "fallback_rate": round(fallbacks / total, 3),
        }
