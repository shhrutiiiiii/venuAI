"""Deep Research v2 endpoints (Wave B / B4). Contract: see research_jobs.py.

  POST   /research/jobs/plan          plan only (1 cheap LLM call)
  POST   /research/jobs               start a job -> 202 {job_id, status}
  GET    /research/jobs               list the caller's recent jobs
  GET    /research/jobs/{id}          status / progress / report (polling)
  GET    /research/jobs/{id}/events   SSE (polling GET above is the required fallback)
  DELETE /research/jobs/{id}          cancel
  POST   /research/jobs/{id}/resume   resume an interrupted/failed job

Everything is scoped to the caller's tenant + user; other users' jobs are 404.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from VENUAI import research_jobs, simple_rate_limit
from VENUAI.auth import AuthenticatedUser, get_current_user

logger = logging.getLogger("drdo-rag")
router = APIRouter(tags=["research"])

Depth = Literal["quick", "standard", "deep"]


class SubQuestionIn(BaseModel):
    id: Optional[str] = Field(default=None, max_length=20)
    text: str = Field(min_length=1, max_length=500)
    rationale: str = Field(default="", max_length=300)


class PlanRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    doc_ids: Optional[List[str]] = None
    depth: Depth = "quick"


class JobRequest(BaseModel):
    plan_id: Optional[str] = None
    question: Optional[str] = Field(default=None, max_length=4000)
    sub_questions: Optional[List[SubQuestionIn]] = None
    doc_ids: Optional[List[str]] = None
    depth: Optional[Depth] = None


def _visible_doc_ids(user: AuthenticatedUser) -> Optional[List[str]]:
    from VENUAI.rag_server import get_visible_doc_ids  # lazy: rag_server imports this router at startup
    return get_visible_doc_ids(user)


def _scope_docs(user: AuthenticatedUser, requested: Optional[List[str]]) -> Optional[List[str]]:
    """Authorized doc ids, narrowed to `requested` when given (never widened)."""
    visible = _visible_doc_ids(user)
    if requested is None:
        return visible
    from VENUAI import authz
    tenant = authz.tenant_of(visible) or user.tenant_id
    if visible is None:
        return authz.ScopedDocIds(requested, tenant_id=tenant)
    allowed = set(visible)
    return authz.ScopedDocIds((d for d in requested if d in allowed), tenant_id=tenant)


def _guard(text: str, user: AuthenticatedUser) -> None:
    from VENUAI.guardrails import get_guardrail_engine
    res = get_guardrail_engine().validate_input(text, user_id=user.id)
    if not res.passed:
        raise HTTPException(status_code=400, detail={
            "blocked": True, "block_reason": res.block_reason or res.violation_type,
            "message": f"Guardrail triggered: {res.violation_type}"})


@router.post("/research/jobs/plan")
def plan_job(body: PlanRequest, user: AuthenticatedUser = Depends(get_current_user)) -> dict:
    simple_rate_limit.check("research_plan", user.id, 10)
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")
    _guard(body.question, user)
    from VENUAI.research_planner import plan_structured

    budget = research_jobs.budget_for(body.depth)
    subs, is_fallback = plan_structured(body.question, budget["max_subquestions"])
    subs = research_jobs.normalize_sub_questions(subs, body.depth)
    store = research_jobs.get_manager().store
    plan_id = store.save_plan(user.tenant_id, user.id, body.question, body.doc_ids, body.depth,
                              subs, budget, is_fallback)
    return {"plan_id": plan_id, "sub_questions": subs, "budget": budget, "plan_is_fallback": is_fallback}


@router.post("/research/jobs", status_code=202)
def create_job(body: JobRequest, user: AuthenticatedUser = Depends(get_current_user)) -> dict:
    simple_rate_limit.check("research_job", user.id, 5)
    mgr = research_jobs.get_manager()
    plan = None
    if body.plan_id:
        plan = mgr.store.get_plan(body.plan_id, user.tenant_id, user.id)
        if plan is None:
            raise HTTPException(status_code=404, detail="Plan not found.")
    question = (body.question or (plan or {}).get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")
    depth = body.depth or (plan or {}).get("depth") or "quick"
    _guard(question, user)

    if body.sub_questions:
        subs = [s.model_dump() for s in body.sub_questions]
        for s in subs:
            _guard(s["text"], user)
        is_fallback = False
    elif plan:
        subs, is_fallback = plan["sub_questions"], bool(plan["is_fallback"])
    else:
        from VENUAI.research_planner import plan_structured
        subs, is_fallback = plan_structured(question, research_jobs.budget_for(depth)["max_subquestions"])
    subs = research_jobs.normalize_sub_questions(subs, depth)
    if not subs:
        raise HTTPException(status_code=400, detail="At least one sub-question is required.")

    requested = body.doc_ids if body.doc_ids is not None else (plan or {}).get("doc_ids")
    allowed = _scope_docs(user, requested)
    job_id = mgr.store.create_job(user.tenant_id, user.id, question, allowed, depth, subs, is_fallback)
    mgr.submit(job_id)
    return {"job_id": job_id, "status": "queued"}


@router.get("/research/jobs")
def list_jobs(limit: int = 20, user: AuthenticatedUser = Depends(get_current_user)) -> List[dict]:
    return research_jobs.get_manager().store.list_jobs(user.tenant_id, user.id, max(1, min(limit, 100)))


@router.get("/research/jobs/{job_id}")
def get_job(job_id: str, user: AuthenticatedUser = Depends(get_current_user)) -> dict:
    try:
        return research_jobs.get_manager().view(job_id, user.tenant_id, user.id)
    except research_jobs.JobNotFound:
        raise HTTPException(status_code=404, detail="Job not found.")


@router.get("/research/jobs/{job_id}/events")
async def job_events(job_id: str, user: AuthenticatedUser = Depends(get_current_user)):
    mgr = research_jobs.get_manager()
    try:
        mgr.view(job_id, user.tenant_id, user.id)  # 404 before the stream starts
    except research_jobs.JobNotFound:
        raise HTTPException(status_code=404, detail="Job not found.")

    async def gen():
        seq = 0
        idle = 0
        while True:
            events = mgr.store.events_since(job_id, seq)
            for s, payload in events:
                seq = s
                yield f"data: {json.dumps(payload)}\n\n"
                if payload.get("done"):
                    return
            row = mgr.store._job_row(job_id, user.tenant_id, user.id)
            if row is None:
                return
            if row["status"] in research_jobs.TERMINAL | {"interrupted"} and not events:
                yield f"data: {json.dumps({'done': True, 'status': row['status']})}\n\n"
                return
            idle = 0 if events else idle + 1
            if idle and idle % 30 == 0:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.delete("/research/jobs/{job_id}")
def cancel_job(job_id: str, user: AuthenticatedUser = Depends(get_current_user)) -> dict:
    try:
        return research_jobs.get_manager().cancel(job_id, user.tenant_id, user.id)
    except research_jobs.JobNotFound:
        raise HTTPException(status_code=404, detail="Job not found.")


@router.post("/research/jobs/{job_id}/resume", status_code=202)
def resume_job(job_id: str, user: AuthenticatedUser = Depends(get_current_user)) -> dict:
    try:
        return research_jobs.get_manager().resume(job_id, user.tenant_id, user.id)
    except research_jobs.JobNotFound:
        raise HTTPException(status_code=404, detail="Job not found.")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
