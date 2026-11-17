"""
Token budgets: per-request ceiling + per-tenant daily counter (SQLite).

  REQUEST_TOKEN_BUDGET        default 40000  tokens one request may spend (0 = off)
  TENANT_DAILY_TOKEN_BUDGET   default 2000000 tokens per tenant per UTC day (0 = off)
  BUDGET_DOWNSHIFT_AT         default 0.8    fraction of either budget after which the
                                             pipeline downshifts to the fast model, no
                                             escalation / corrective regeneration.
At 100% of the tenant budget the request is refused with a clear message (HTTP 429);
at 100% of the per-request ceiling further LLM calls raise BudgetExceeded and the turn
returns what it has. Counts are provider-reported tokens only.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "data" / "token_budget.db"
_lock = threading.Lock()


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def request_ceiling() -> int:
    return _int_env("REQUEST_TOKEN_BUDGET", 40000)


def tenant_daily_budget() -> int:
    return _int_env("TENANT_DAILY_TOKEN_BUDGET", 2_000_000)


def downshift_fraction() -> float:
    try:
        return float(os.getenv("BUDGET_DOWNSHIFT_AT", "0.8"))
    except ValueError:
        return 0.8


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.execute("CREATE TABLE IF NOT EXISTS tenant_tokens (tenant_id TEXT, day TEXT, tokens INTEGER, "
              "PRIMARY KEY (tenant_id, day))")
    return c


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def used_today(tenant_id: str) -> int:
    try:
        with _lock, _conn() as c:
            r = c.execute("SELECT tokens FROM tenant_tokens WHERE tenant_id=? AND day=?",
                          (tenant_id, _today())).fetchone()
        return int(r[0]) if r else 0
    except sqlite3.Error:
        return 0


def add_usage(tenant_id: str, tokens: int) -> None:
    if not tokens or tokens < 0:
        return
    try:
        with _lock, _conn() as c:
            c.execute("INSERT INTO tenant_tokens VALUES (?,?,?) ON CONFLICT(tenant_id, day) "
                      "DO UPDATE SET tokens = tokens + excluded.tokens", (tenant_id, _today(), int(tokens)))
    except sqlite3.Error:
        pass


def tenant_status(tenant_id: str) -> str:
    """'ok' | 'downshift' | 'exhausted'"""
    cap = tenant_daily_budget()
    if cap <= 0:
        return "ok"
    used = used_today(tenant_id)
    if used >= cap:
        return "exhausted"
    return "downshift" if used >= cap * downshift_fraction() else "ok"


EXHAUSTED_MESSAGE = ("Your organisation's daily AI token budget has been used up. "
                     "Please try again tomorrow or ask an administrator to raise TENANT_DAILY_TOKEN_BUDGET.")


def meter_downshift(meter) -> bool:
    """True when this request has spent >= downshift fraction of its ceiling."""
    cap = meter.ceiling if meter is not None else None
    return bool(cap) and meter.total_tokens >= cap * downshift_fraction()
