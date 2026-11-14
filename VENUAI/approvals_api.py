"""Approvals HTTP API (Wave B / B3). Auto-included by the rag_server router hook."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from VENUAI import approvals, auth, authz

router = APIRouter()


class DecisionBody(BaseModel):
    approve: bool
    remember: bool = False


class PinApproveBody(BaseModel):
    server: str
    tool: str


@router.get("/approvals/pending")
def pending(user: auth.AuthenticatedUser = Depends(auth.get_current_user)):
    return approvals.list_pending(user)


@router.post("/approvals/{approval_id}/decision")
def decision(approval_id: str, body: DecisionBody,
             user: auth.AuthenticatedUser = Depends(auth.get_current_user)):
    out = approvals.decide(approval_id, user, body.approve, body.remember)
    if out is None:
        raise HTTPException(status_code=404, detail="Approval not found.")
    return out


@router.get("/approvals/pins")
def pins(user: auth.AuthenticatedUser = Depends(authz.require_super_admin)):
    """Operator (SUPERADMIN_USER_IDS): MCP tool pins are server-wide, not per tenant,
    so a tenant admin must not see or re-approve them (status 'drifted' = disabled
    until re-approved)."""
    return approvals.list_pins()


@router.post("/approvals/pins/approve")
def approve_pin(body: PinApproveBody, user: auth.AuthenticatedUser = Depends(authz.require_super_admin)):
    if not approvals.approve_pin(body.server, body.tool, user.id):
        raise HTTPException(status_code=404, detail="No drifted pin for that tool.")
    return {"server": body.server, "tool": body.tool, "status": "pinned"}
