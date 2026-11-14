"""Human-in-the-loop approvals + MCP tool-description pinning (Wave B / B3).

Policy layers (see ``policy_engine``): catalog -> role allow-list -> approval.
This module is the approval layer.

``TOOL_APPROVAL`` env:
  unset (default) : MCP tools need approval; built-ins only if
                    ``ToolDescriptor.requires_approval`` AND explicitly turned on
                    (so default UX/tests are unchanged for built-ins).
  ``on``          : MCP tools and every built-in flagged ``requires_approval``.
  ``off``         : no approvals (explicit operator opt-out).

State is persisted (SQLite) so a pending approval survives restarts; every row
is tenant- and user-scoped. Pinning: ``sha256(name + description + inputSchema)``
per MCP tool; any drift disables the tool until an admin re-approves the pin.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("drdo-rag")

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "approvals.db"
PENDING_TTL_S = 3600          # an undecided request expires after 1h
APPROVED_TTL_S = 900          # an approved-but-unused request expires after 15 min

_lock = threading.RLock()
_ready_for: Optional[str] = None
_EXECUTORS: dict[str, Callable[[dict], Any]] = {}
_PENDING_BY_USER: dict[str, list[dict]] = {}


@dataclass
class ApprovalDecision:
    status: str                      # "allowed" | "pending" | "denied"
    reason: str = ""
    approval_id: Optional[str] = None
    args_preview: str = ""
    description_hash: str = ""
    approver: Optional[str] = None

    @property
    def allowed(self) -> bool:
        return self.status == "allowed"

    def pending_payload(self, tool: str) -> dict:
        return {"id": self.approval_id, "tool": tool, "args_preview": self.args_preview,
                "description_hash": self.description_hash}


def _conn() -> sqlite3.Connection:
    global _ready_for
    DATA_DIR.mkdir(exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), timeout=5)
    c.row_factory = sqlite3.Row
    if _ready_for != str(DB_PATH):
        c.executescript("""
            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
                tool TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'builtin',
                args_json TEXT NOT NULL, args_sha256 TEXT NOT NULL, args_preview TEXT,
                description_hash TEXT NOT NULL, status TEXT NOT NULL,
                remember INTEGER NOT NULL DEFAULT 0, consumed INTEGER NOT NULL DEFAULT 0,
                approver TEXT, created_at REAL NOT NULL, decided_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_appr_user ON approvals(tenant_id, user_id, status);
            CREATE TABLE IF NOT EXISTS mcp_tool_pins (
                server TEXT NOT NULL, tool TEXT NOT NULL, sha256 TEXT NOT NULL,
                observed_sha256 TEXT, status TEXT NOT NULL DEFAULT 'pinned',
                pinned_at REAL NOT NULL, approved_by TEXT,
                PRIMARY KEY (server, tool)
            );
        """)
        c.commit()
        _ready_for = str(DB_PATH)
    return c


# ── hashing / pinning ────────────────────────────────────────────────────────
def description_hash(name: str, description: str, schema: Any) -> str:
    try:
        sch = json.dumps(schema, sort_keys=True, default=str, separators=(",", ":"))
    except Exception:
        sch = repr(schema)
    return hashlib.sha256(f"{name}\x00{description or ''}\x00{sch}".encode("utf-8")).hexdigest()


def pin_tool(server: str, tool: str, description: str, schema: Any) -> str:
    """Record/verify the pin. Returns ``"new"`` (first sight, pinned), ``"ok"`` or ``"drift"``
    (hash changed since pinned: the tool must stay disabled until ``approve_pin``)."""
    h = description_hash(tool, description, schema)
    with _lock:
        c = _conn()
        try:
            row = c.execute("SELECT * FROM mcp_tool_pins WHERE server=? AND tool=?", (server, tool)).fetchone()
            if row is None:
                c.execute("INSERT INTO mcp_tool_pins (server, tool, sha256, status, pinned_at) VALUES (?,?,?,?,?)",
                          (server, tool, h, "pinned", time.time()))
                c.commit()
                return "new"
            if row["sha256"] == h:
                if row["status"] == "drifted":   # drifted earlier, reverted to the pinned definition
                    c.execute("UPDATE mcp_tool_pins SET status='pinned', observed_sha256=NULL WHERE server=? AND tool=?",
                              (server, tool))
                    c.commit()
                return "ok"
            c.execute("UPDATE mcp_tool_pins SET status='drifted', observed_sha256=? WHERE server=? AND tool=?",
                      (h, server, tool))
            c.commit()
            logger.warning("[approvals] MCP tool drift: %s/%s changed since it was pinned - disabled", server, tool)
            return "drift"
        finally:
            c.close()


def approve_pin(server: str, tool: str, approver: str) -> bool:
    """Admin re-approval after drift: adopt the observed hash as the new pin."""
    with _lock:
        c = _conn()
        try:
            row = c.execute("SELECT * FROM mcp_tool_pins WHERE server=? AND tool=?", (server, tool)).fetchone()
            if row is None or row["status"] != "drifted" or not row["observed_sha256"]:
                return False
            c.execute("UPDATE mcp_tool_pins SET sha256=?, observed_sha256=NULL, status='pinned', approved_by=?,"
                      " pinned_at=? WHERE server=? AND tool=?",
                      (row["observed_sha256"], approver, time.time(), server, tool))
            c.commit()
            return True
        finally:
            c.close()


def list_pins() -> list[dict]:
    c = _conn()
    try:
        return [dict(r) for r in c.execute("SELECT * FROM mcp_tool_pins ORDER BY server, tool").fetchall()]
    finally:
        c.close()


# ── policy ───────────────────────────────────────────────────────────────────
def approval_required(tool_id: str, kind: str = "builtin") -> bool:
    mode = os.getenv("TOOL_APPROVAL", "").strip().lower()
    if mode == "off":
        return False
    if kind == "mcp":
        return True
    if mode != "on":
        return False
    try:
        from VENUAI import tool_registry
        d = tool_registry.get(tool_id)
        return bool(d and getattr(d, "requires_approval", False))
    except Exception:
        return False


def _preview(args: Any) -> str:
    try:
        s = json.dumps(args, default=str, ensure_ascii=False)
    except Exception:
        s = repr(args)
    try:
        from VENUAI.pii_redaction import redact
        s = redact(s)
    except Exception:
        pass
    return s if len(s) <= 300 else s[:297] + "..."


def _args_hash(args: Any) -> str:
    from VENUAI.trace_store import sha256_json
    return sha256_json(args)


def require_approval(tool_name: str, args: Any, user: Any, *, description: str = "",
                     schema: Any = None, kind: str = "builtin") -> ApprovalDecision:
    """Contract entry point. ``user`` needs ``.id`` and ``.tenant_id``."""
    dh = description_hash(tool_name, description, schema)
    if not approval_required(tool_name, kind):
        return ApprovalDecision("allowed", "approval not required", description_hash=dh)
    ah = _args_hash(args)
    now = time.time()
    with _lock:
        c = _conn()
        try:
            base = (user.tenant_id, user.id, tool_name, dh)
            # remembered approval for this exact tool definition
            r = c.execute("SELECT id, approver FROM approvals WHERE tenant_id=? AND user_id=? AND tool=? "
                          "AND description_hash=? AND status='approved' AND remember=1 LIMIT 1", base).fetchone()
            if r:
                return ApprovalDecision("allowed", "remembered approval", r["id"], description_hash=dh,
                                        approver=r["approver"])
            rows = c.execute("SELECT * FROM approvals WHERE tenant_id=? AND user_id=? AND tool=? "
                             "AND description_hash=? AND args_sha256=? AND consumed=0 ORDER BY created_at DESC",
                             base + (ah,)).fetchall()
            for row in rows:
                if row["status"] == "approved" and now - (row["decided_at"] or 0) <= APPROVED_TTL_S:
                    c.execute("UPDATE approvals SET consumed=1 WHERE id=?", (row["id"],))
                    c.commit()
                    return ApprovalDecision("allowed", "approved", row["id"], description_hash=dh,
                                            approver=row["approver"])
                if row["status"] == "denied":
                    c.execute("UPDATE approvals SET consumed=1 WHERE id=?", (row["id"],))
                    c.commit()
                    return ApprovalDecision("denied", "denied by approver", row["id"], description_hash=dh,
                                            approver=row["approver"])
                if row["status"] == "pending" and now - row["created_at"] <= PENDING_TTL_S:
                    return _pending(user, tool_name, row["id"], row["args_preview"], dh)
            aid = uuid.uuid4().hex
            prev = _preview(args)
            c.execute("INSERT INTO approvals (id, tenant_id, user_id, tool, kind, args_json, args_sha256,"
                      " args_preview, description_hash, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (aid, user.tenant_id, user.id, tool_name, kind, json.dumps(args, default=str), ah,
                       prev, dh, "pending", now))
            c.commit()
            return _pending(user, tool_name, aid, prev, dh)
        finally:
            c.close()


def _pending(user: Any, tool: str, aid: str, preview: str, dh: str) -> ApprovalDecision:
    d = ApprovalDecision("pending", "awaiting human approval", aid, preview, dh)
    _PENDING_BY_USER.setdefault(user.id, []).append(d.pending_payload(tool))
    return d


def pop_pending(user_id: str) -> list[dict]:
    """Pending approvals raised during the current request (for ``pending_approval`` in /query)."""
    return _PENDING_BY_USER.pop(user_id, [])


def _visible(row: sqlite3.Row, user: Any) -> bool:
    return row["tenant_id"] == user.tenant_id and (user.role == "admin" or row["user_id"] == user.id)


def list_pending(user: Any) -> list[dict]:
    c = _conn()
    try:
        rows = c.execute("SELECT id, user_id, tool, args_preview, description_hash, created_at FROM approvals "
                         "WHERE tenant_id=? AND status='pending' AND created_at>=? ORDER BY created_at",
                         (user.tenant_id, time.time() - PENDING_TTL_S)).fetchall()
        return [{"id": r["id"], "tool": r["tool"], "args_preview": r["args_preview"],
                 "description_hash": r["description_hash"], "created_at": r["created_at"]}
                for r in rows if user.role == "admin" or r["user_id"] == user.id]
    finally:
        c.close()


def decide(approval_id: str, user: Any, approve: bool, remember: bool = False) -> Optional[dict]:
    """Returns the updated row summary, or ``None`` if not found/not visible (=> 404)."""
    with _lock:
        c = _conn()
        try:
            row = c.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
            if row is None or not _visible(row, user):
                return None
            if row["status"] != "pending":
                return {"id": approval_id, "status": row["status"], "already_decided": True}
            if time.time() - row["created_at"] > PENDING_TTL_S:
                c.execute("UPDATE approvals SET status='expired' WHERE id=?", (approval_id,))
                c.commit()
                return {"id": approval_id, "status": "expired"}
            status = "approved" if approve else "denied"
            c.execute("UPDATE approvals SET status=?, remember=?, approver=?, decided_at=? WHERE id=?",
                      (status, int(bool(remember and approve)), user.id, time.time(), approval_id))
            c.commit()
            try:
                from VENUAI import tracing
                tracing.record_tool_call(row["tool"], {"approval_id": approval_id}, decision=status,
                                         approver=user.id, status="ok", tenant_id=row["tenant_id"],
                                         user_id=row["user_id"])
            except Exception:
                pass
            return {"id": approval_id, "status": status, "tool": row["tool"]}
        finally:
            c.close()


def register_executor(tool_name: str, fn: Callable[[dict], Any]) -> None:
    """Stateless executors used by ``resume`` (identity-free: ``fn(args) -> result``)."""
    _EXECUTORS[tool_name] = fn


def resume(approval_id: str, user: Any) -> dict:
    """Run an approved call (delegated to by B1's ``POST /query/resume``).
    Returns ``{"status": ..., "tool": ..., "result"?: ...}``; status is one of
    ``completed | not_found | not_approved | already_used | failed | no_executor``."""
    with _lock:
        c = _conn()
        try:
            row = c.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
            if row is None or not _visible(row, user):
                return {"status": "not_found"}
            if row["status"] != "approved":
                return {"status": "not_approved", "approval_status": row["status"], "tool": row["tool"]}
            if row["consumed"] or time.time() - (row["decided_at"] or 0) > APPROVED_TTL_S:
                return {"status": "already_used", "tool": row["tool"]}
            if row["kind"] == "builtin":
                from VENUAI.policy_engine import check_tool_call
                if not check_tool_call(user.role, "react_agent", row["tool"]).allowed:
                    return {"status": "not_approved", "tool": row["tool"], "reason": "policy"}
            fn = _EXECUTORS.get(row["tool"])
            if fn is None:
                return {"status": "no_executor", "tool": row["tool"]}
            c.execute("UPDATE approvals SET consumed=1 WHERE id=?", (approval_id,))
            c.commit()
            args = json.loads(row["args_json"])
            tool = row["tool"]
        finally:
            c.close()
    t0 = time.perf_counter()
    try:
        result = fn(args)
        status, err = "ok", None
    except Exception as e:
        result, status, err = None, "error", str(e)
    try:
        from VENUAI import tracing
        tracing.record_tool_call(tool, args, decision="approved", approver=row["approver"], status=status,
                                 latency_ms=(time.perf_counter() - t0) * 1000, error=err,
                                 tenant_id=row["tenant_id"], user_id=row["user_id"])
    except Exception:
        pass
    if err:
        return {"status": "failed", "tool": tool, "error": err}
    return {"status": "completed", "tool": tool, "result": result}
