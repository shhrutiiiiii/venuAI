"""Long-term memory endpoints (Wave B / B4). Always scoped to the caller's
tenant + user; memories of other users are invisible (404 on delete)."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from VENUAI.auth import AuthenticatedUser, get_current_user

router = APIRouter(tags=["memory"])


class MemorySettings(BaseModel):
    enabled: bool


def _store():
    from VENUAI import memory_manager
    return memory_manager.get_ltm_store()


@router.get("/memory")
def list_memories(user: AuthenticatedUser = Depends(get_current_user)) -> List[dict]:
    return _store().list(user.tenant_id, user.id)


@router.get("/memory/settings")
def get_memory_settings(user: AuthenticatedUser = Depends(get_current_user)) -> dict:
    from VENUAI import memory_manager
    return {
        "enabled": _store().user_enabled(user.tenant_id, user.id),
        # LONG_TERM_MEMORY=on must also be set server-side for anything to be stored/recalled.
        "feature_enabled": memory_manager.long_term_memory_enabled(),
    }


@router.put("/memory/settings")
def put_memory_settings(body: MemorySettings, user: AuthenticatedUser = Depends(get_current_user)) -> dict:
    from VENUAI import memory_manager
    _store().set_enabled(user.tenant_id, user.id, body.enabled)
    return {"enabled": body.enabled, "feature_enabled": memory_manager.long_term_memory_enabled()}


@router.delete("/memory/{memory_id}")
def delete_memory(memory_id: str, user: AuthenticatedUser = Depends(get_current_user)) -> dict:
    if not _store().delete(user.tenant_id, user.id, memory_id):
        raise HTTPException(status_code=404, detail="Memory not found.")
    return {"deleted": 1}


@router.delete("/memory")
def forget_all(user: AuthenticatedUser = Depends(get_current_user)) -> dict:
    return {"deleted": _store().delete_all(user.tenant_id, user.id)}
