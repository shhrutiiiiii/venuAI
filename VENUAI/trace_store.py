"""Persisted trace store + always-on LLM/tool-call ledgers (Wave B / B3).

SQLite, same pattern as ``ai_gateway/telemetry.py``. Tenant-scoped on every
read. No question/answer content is stored unless ``TRACE_STORE_CONTENT=on``
(and then only PII-redacted). Every public function swallows its own errors:
tracing must never break a request.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("drdo-rag")

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "traces.db"
TRACE_TTL_SECONDS = int(os.getenv("TRACE_TTL_DAYS", "14")) * 86400

_lock = threading.RLock()
_ready_for: Optional[str] = None
_last_purge = 0.0


def _conn() -> sqlite3.Connection:
    global _ready_for
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    if _ready_for != str(DB_PATH):
        _init(conn)
        _ready_for = str(DB_PATH)
    return conn


def _init(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS traces (
            trace_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            path TEXT,
            question_hash TEXT,
            question TEXT,
            started_at REAL NOT NULL,
            total_ms REAL,
            payload TEXT NOT NULL,
            online_eval TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_traces_tenant ON traces(tenant_id, user_id, started_at);
        CREATE TABLE IF NOT EXISTS llm_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT, tenant_id TEXT, ts REAL NOT NULL,
            model TEXT, task TEXT, prompt_tokens INTEGER, completion_tokens INTEGER,
            latency_ms REAL, cached INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_llm_calls_ts ON llm_calls(ts);
        CREATE TABLE IF NOT EXISTS tool_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT, tenant_id TEXT, user_id TEXT, ts REAL NOT NULL,
            tool TEXT NOT NULL, args_sha256 TEXT, decision TEXT, approver TEXT,
            status TEXT, latency_ms REAL, error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_tool_calls_ts ON tool_calls(ts);
        """
    )
    conn.commit()


def sha256_json(obj: Any) -> str:
    """Stable hash of tool args (the ledger stores this, never the args)."""
    try:
        raw = json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False)
    except Exception:
        raw = repr(obj)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def question_hash(question: str) -> str:
    return hashlib.sha256((question or "").encode("utf-8")).hexdigest()[:16]


def _redact(text: str) -> str:
    try:
        from VENUAI.pii_redaction import redact
        return redact(text)
    except Exception:
        return text


def save_trace(trace: dict) -> None:
    """Persist one finished trace dict (see tracing.finish_trace)."""
    global _last_purge
    try:
        content = os.getenv("TRACE_STORE_CONTENT", "off").lower() == "on"
        q = trace.get("question")
        stored_q = _redact(str(q))[:2000] if (content and q) else None
        payload = {k: v for k, v in trace.items()
                   if k not in ("question", "tenant_id", "user_id", "online_eval")}
        with _lock:
            conn = _conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO traces (trace_id, tenant_id, user_id, path, question_hash,"
                    " question, started_at, total_ms, payload, online_eval) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (trace["trace_id"], trace.get("tenant_id") or "default", trace.get("user_id") or "",
                     trace.get("path"), trace.get("question_hash"), stored_q,
                     trace.get("started_at") or time.time(), trace.get("total_ms"),
                     json.dumps(payload, default=str),
                     json.dumps(trace["online_eval"]) if trace.get("online_eval") else None),
                )
                for c in trace.get("llm_calls", []):
                    conn.execute(
                        "INSERT INTO llm_calls (trace_id, tenant_id, ts, model, task, prompt_tokens,"
                        " completion_tokens, latency_ms, cached) VALUES (?,?,?,?,?,?,?,?,?)",
                        (trace["trace_id"], trace.get("tenant_id"), c.get("ts") or time.time(),
                         c.get("model"), c.get("task"), c.get("prompt_tokens"),
                         c.get("completion_tokens"), c.get("latency_ms"), int(bool(c.get("cached")))),
                    )
                conn.commit()
                now = time.time()
                if now - _last_purge > 3600:
                    _last_purge = now
                    purge_expired(conn=conn)
            finally:
                conn.close()
    except Exception as e:  # never raise
        logger.debug("trace save failed: %s", e)


def purge_expired(ttl_seconds: Optional[int] = None, conn: Optional[sqlite3.Connection] = None) -> int:
    ttl = TRACE_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    cutoff = time.time() - ttl
    own = conn is None
    try:
        c = conn or _conn()
        cur = c.execute("DELETE FROM traces WHERE started_at < ?", (cutoff,))
        c.execute("DELETE FROM llm_calls WHERE ts < ?", (cutoff,))
        c.commit()
        n = cur.rowcount
        if own:
            c.close()
        return n
    except Exception as e:
        logger.debug("trace purge failed: %s", e)
        return 0


def _row_to_trace(row: sqlite3.Row) -> dict:
    d = json.loads(row["payload"])
    d.update(
        trace_id=row["trace_id"], tenant_id=row["tenant_id"], user_id=row["user_id"],
        path=row["path"], question_hash=row["question_hash"], started_at=row["started_at"],
        total_ms=row["total_ms"],
    )
    if row["question"]:
        d["question"] = row["question"]
    if row["online_eval"]:
        d["online_eval"] = json.loads(row["online_eval"])
    return d


def get_trace(trace_id: str, tenant_id: str, user_id: Optional[str] = None) -> Optional[dict]:
    """Tenant-scoped read. ``user_id=None`` => any owner in the tenant (admin)."""
    try:
        conn = _conn()
        try:
            sql = "SELECT * FROM traces WHERE trace_id = ? AND tenant_id = ?"
            args: list = [trace_id, tenant_id]
            if user_id is not None:
                sql += " AND user_id = ?"
                args.append(user_id)
            row = conn.execute(sql, args).fetchone()
            return _row_to_trace(row) if row else None
        finally:
            conn.close()
    except Exception as e:
        logger.debug("trace read failed: %s", e)
        return None


def list_traces(tenant_id: str, user_id: Optional[str] = None, limit: int = 20) -> list[dict]:
    try:
        conn = _conn()
        try:
            sql = ("SELECT trace_id, user_id, path, question_hash, started_at, total_ms, payload "
                   "FROM traces WHERE tenant_id = ?")
            args: list = [tenant_id]
            if user_id is not None:
                sql += " AND user_id = ?"
                args.append(user_id)
            sql += " ORDER BY started_at DESC LIMIT ?"
            args.append(max(1, min(int(limit), 200)))
            out = []
            for r in conn.execute(sql, args).fetchall():
                p = json.loads(r["payload"])
                out.append({
                    "trace_id": r["trace_id"], "path": r["path"], "started_at": r["started_at"],
                    "total_ms": r["total_ms"], "question_hash": r["question_hash"],
                    "cache_hit": p.get("cache_hit"), "model_path": p.get("model_path"),
                })
            return out
        finally:
            conn.close()
    except Exception as e:
        logger.debug("trace list failed: %s", e)
        return []


def set_online_eval(trace_id: str, result: dict) -> None:
    try:
        with _lock:
            conn = _conn()
            try:
                conn.execute("UPDATE traces SET online_eval = ? WHERE trace_id = ?",
                             (json.dumps(result), trace_id))
                conn.commit()
            finally:
                conn.close()
    except Exception as e:
        logger.debug("online eval store failed: %s", e)


def recent_online_evals(limit: int = 50) -> list[dict]:
    try:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT online_eval FROM traces WHERE online_eval IS NOT NULL "
                "ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
            return [json.loads(r["online_eval"]) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


# ── ledgers ──────────────────────────────────────────────────────────────────
def record_tool_call(tool: str, args: Any, *, tenant_id: str = "", user_id: str = "",
                     trace_id: Optional[str] = None, decision: str = "allowed",
                     approver: Optional[str] = None, status: str = "ok",
                     latency_ms: Optional[float] = None, error: Optional[str] = None) -> None:
    """Always-on tool ledger: stores ``args_sha256``, never the raw args."""
    try:
        with _lock:
            conn = _conn()
            try:
                conn.execute(
                    "INSERT INTO tool_calls (trace_id, tenant_id, user_id, ts, tool, args_sha256,"
                    " decision, approver, status, latency_ms, error) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (trace_id, tenant_id, user_id, time.time(), tool, sha256_json(args), decision,
                     approver, status, latency_ms, (error or "")[:300] or None),
                )
                conn.commit()
            finally:
                conn.close()
    except Exception as e:
        logger.debug("tool ledger write failed: %s", e)


def tool_calls(tenant_id: str, limit: int = 100) -> list[dict]:
    try:
        conn = _conn()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM tool_calls WHERE tenant_id = ? ORDER BY id DESC LIMIT ?",
                (tenant_id, limit)).fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


def _pct(sorted_vals: list, q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return float(sorted_vals[idx])


def ops_metrics(days: int = 7, tenant_id: Optional[str] = None) -> dict:
    """Aggregates over the ledgers: tokens, p50/p95 latency, cache hit rate, escalation rate."""
    since = time.time() - days * 86400
    out: dict = {"window_days": days, "llm_calls": 0, "tokens": {"prompt": 0, "completion": 0},
                 "latency_ms": {"p50": None, "p95": None}, "cache_hit_rate": None,
                 "escalation_rate": None, "by_model": {}, "tool_calls": 0, "tool_errors": 0}
    try:
        conn = _conn()
        try:
            tf = " AND tenant_id = ?" if tenant_id else ""
            ta = [since] + ([tenant_id] if tenant_id else [])
            rows = conn.execute(
                f"SELECT model, prompt_tokens, completion_tokens, latency_ms, cached FROM llm_calls "
                f"WHERE ts >= ?{tf}", ta).fetchall()
            lat = sorted(r["latency_ms"] for r in rows if r["latency_ms"] is not None and not r["cached"])
            out["llm_calls"] = len(rows)
            for r in rows:
                out["tokens"]["prompt"] += r["prompt_tokens"] or 0
                out["tokens"]["completion"] += r["completion_tokens"] or 0
                m = out["by_model"].setdefault(
                    r["model"] or "unknown", {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
                m["calls"] += 1
                m["prompt_tokens"] += r["prompt_tokens"] or 0
                m["completion_tokens"] += r["completion_tokens"] or 0
            if lat:
                out["latency_ms"] = {"p50": round(_pct(lat, 0.5), 1), "p95": round(_pct(lat, 0.95), 1)}
            n = hits = esc = 0
            for r in conn.execute(f"SELECT payload FROM traces WHERE started_at >= ?{tf}", ta).fetchall():
                p = json.loads(r["payload"])
                n += 1
                hits += 1 if p.get("cache_hit") else 0
                esc += 1 if len(p.get("model_path") or []) > 1 else 0
            if n:
                out["cache_hit_rate"] = round(hits / n, 3)
                out["escalation_rate"] = round(esc / n, 3)
            tc = conn.execute(
                f"SELECT COUNT(*) c, SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END) e FROM tool_calls "
                f"WHERE ts >= ?{tf}", ta).fetchone()
            out["tool_calls"], out["tool_errors"] = tc["c"] or 0, tc["e"] or 0
        finally:
            conn.close()
    except Exception as e:
        logger.debug("ops metrics failed: %s", e)
    return out
