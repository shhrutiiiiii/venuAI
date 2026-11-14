"""A2A (Agent2Agent) agent card + message:send (Wave B / B3).

Opt-in: everything returns 404 unless ``A2A_ENABLED=on``. The agent card is
public (no secrets); ``message:send`` requires a VenuAI bearer token, is
tenant-scoped (document visibility of the calling user) and rate limited
(``A2A_RATE_LIMIT_PER_MIN``, default 20 per user).

Two request shapes are accepted on ``POST /a2a/v1/message:send``:
  * JSON-RPC style: ``{"jsonrpc":"2.0","id":..,"method":"message/send","params":{"message":{...}}}``
  * plain: ``{"message": {"role":"user","parts":[{"kind":"text","text":"..."}]}}``

The question is answered by ``agents.orchestrator.OrchestratorAgent.route``
(public API, not modified here). No answer is fabricated: if the LLM backend
is unavailable the call fails with an error.
"""
from __future__ import annotations

import collections
import os
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from VENUAI import auth

router = APIRouter()
A2A_PROTOCOL_VERSION = "0.3.0"
_hits: dict[str, collections.deque] = {}


def enabled() -> bool:
    return os.getenv("A2A_ENABLED", "off").strip().lower() in ("on", "true", "1")


def _require_enabled() -> None:
    if not enabled():
        raise HTTPException(status_code=404, detail="A2A is not enabled.")


def build_agent_card(base_url: str) -> dict:
    skills = []
    try:
        from VENUAI.agents.registry import all_agents
        for a in all_agents():
            if getattr(a, "enabled", True):
                skills.append({"id": a.agent_id, "name": a.name, "description": a.description,
                               "tags": list(getattr(a, "required_permissions", []) or [])})
    except Exception:
        pass
    return {
        "protocolVersion": A2A_PROTOCOL_VERSION,
        "name": "VenuAI",
        "description": "Grounded, verified question answering over a DRDO research knowledge base.",
        "url": f"{base_url}/a2a/v1",
        "preferredTransport": "JSONRPC",
        "version": "1.0",
        "capabilities": {"streaming": False, "pushNotifications": False, "stateTransitionHistory": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": skills,
        "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}},
        "security": [{"bearer": []}],
    }


def _base_url(request: Request) -> str:
    return (os.getenv("A2A_PUBLIC_URL", "").rstrip("/") or str(request.base_url).rstrip("/"))


@router.get("/.well-known/agent-card.json")
def agent_card(request: Request):
    _require_enabled()
    return build_agent_card(_base_url(request))


def _rate_limit(user_id: str) -> None:
    limit = int(os.getenv("A2A_RATE_LIMIT_PER_MIN", "20"))
    now = time.monotonic()
    q = _hits.setdefault(user_id, collections.deque())
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(status_code=429, detail="A2A rate limit exceeded.")
    q.append(now)


def _extract_text(message: dict) -> str:
    parts = message.get("parts") or []
    return " ".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")).strip()


async def answer_via_orchestrator(question: str, user: auth.AuthenticatedUser) -> dict:
    """Seam for tests: route through the documented orchestrator API with the user's doc scope."""
    from VENUAI.agents.orchestrator import OrchestratorAgent
    from VENUAI.ai_gateway.provider import get_async_groq_client
    from VENUAI.rag_server import get_visible_doc_ids  # lazy: avoids an import cycle at module load

    orch = OrchestratorAgent(get_async_groq_client())
    res = await orch.route(question, [], None, allowed_doc_ids=get_visible_doc_ids(user))
    return {"answer": getattr(res, "answer", ""), "agent": getattr(res, "agent_name", None)}


@router.post("/a2a/v1/message:send")
async def message_send(body: dict, user: auth.AuthenticatedUser = Depends(auth.get_current_user)):
    _require_enabled()
    _rate_limit(user.id)
    rpc_id: Optional[Any] = None
    rpc = body.get("jsonrpc") == "2.0"
    if rpc:
        rpc_id = body.get("id")
        if body.get("method") != "message/send":
            return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": "Method not found"}}
        params = body.get("params") or {}
    else:
        params = body
    message = params.get("message")
    question = _extract_text(message) if isinstance(message, dict) else ""
    if not question or len(question) > 4000:
        err = {"code": -32602, "message": "message.parts must contain 1-4000 chars of text"}
        if rpc:
            return {"jsonrpc": "2.0", "id": rpc_id, "error": err}
        raise HTTPException(status_code=400, detail=err["message"])
    try:
        out = await answer_via_orchestrator(question, user)
    except Exception as e:
        err = {"code": -32000, "message": f"agent failed: {type(e).__name__}"}
        if rpc:
            return {"jsonrpc": "2.0", "id": rpc_id, "error": err}
        raise HTTPException(status_code=502, detail=err["message"])
    reply = {"kind": "message", "role": "agent", "messageId": uuid.uuid4().hex,
             "contextId": message.get("contextId") or uuid.uuid4().hex,
             "parts": [{"kind": "text", "text": out["answer"]}]}
    return {"jsonrpc": "2.0", "id": rpc_id, "result": reply} if rpc else reply
