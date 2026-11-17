"""Trace read API (Wave B / B3). Auto-included by the rag_server router hook.

Scoping: a non-admin only ever sees traces they own; an admin sees traces of
their own tenant. Anything else is a 404 (never 403) so trace ids cannot be
probed across users/tenants.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from VENUAI import auth, trace_store

router = APIRouter()

_PUBLIC_KEYS = ("trace_id", "question_hash", "started_at", "total_ms", "steps", "llm_calls", "retrieved",
                "model_path", "cache_hit", "verification", "prompt_version", "path", "spans",
                "tool_calls", "online_eval", "question")


def _owner_filter(user: auth.AuthenticatedUser):
    return None if user.role == "admin" else user.id


@router.get("/traces")
def list_traces(limit: int = Query(20, ge=1, le=200),
                user: auth.AuthenticatedUser = Depends(auth.get_current_user)):
    return trace_store.list_traces(user.tenant_id, _owner_filter(user), limit)


@router.get("/traces/{trace_id}")
def get_trace(trace_id: str, user: auth.AuthenticatedUser = Depends(auth.get_current_user)):
    t = trace_store.get_trace(trace_id, user.tenant_id, _owner_filter(user))
    if t is None:
        raise HTTPException(status_code=404, detail="Trace not found.")
    return {k: t[k] for k in _PUBLIC_KEYS if k in t}


@router.get("/ops/tool-calls")
def tool_call_ledger(limit: int = Query(100, ge=1, le=500),
                     user: auth.AuthenticatedUser = Depends(auth.require_admin)):
    """Admin: this tenant's tool-call ledger (hashed args, decision, approver)."""
    return trace_store.tool_calls(user.tenant_id, limit)


@router.get("/ops/metrics")
def ops_metrics(days: int = Query(7, ge=1, le=90),
                user: auth.AuthenticatedUser = Depends(auth.require_admin)):
    return trace_store.ops_metrics(days, user.tenant_id)
