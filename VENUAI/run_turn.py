"""
Shared turn pipeline. Every producer of an answer (standard RAG /query, /query/stream,
the ReAct agent, multi-agent routing) funnels through the helpers here so guardrails,
verification, citations, budgets, cache, follow-ups, contradictions, usage and the
response/done-event fields are produced in exactly ONE place:

    begin_turn()        context + usage meter + step sink, budget gate, memory recall
    cache_lookup()      exact (and opt-in semantic) answer cache, scoped by tenant + doc set
    corrective_stage()  capped self-reflective loop (re-retrieve / regenerate / escalate model)
    finish_turn()       response fields, cache write, budget accounting, memory write, trace
    finish_agent_turn() same tail for ReAct / multi-agent (light verification + output safety)

I/O and model calls stay in rag_server (tests patch them there); this module takes the
`deps` module and calls through it so those patches keep working.
See Documents/AI/pipeline.md.
"""
from __future__ import annotations

import hashlib
import inspect
import logging
import queue
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from langchain_core.documents import Document

from VENUAI import corrective_rag, model_cascade, semantic_cache, token_budget, followups as fu
from VENUAI.ai_gateway import usage as _usage

logger = logging.getLogger("drdo-rag")

STEP_IDS = ("cache", "rewriting", "retrieving", "reranking", "reading", "generating",
            "verifying", "correcting", "cascade")


class StepSink:
    """Typed pipeline step events. Collected locally (feeds /query/stream) and mirrored to
    tracing.emit_step (B3) when present."""

    def __init__(self, q: Optional[queue.Queue] = None) -> None:
        self.q = q
        self.t0 = time.perf_counter()
        self.events: List[dict] = []
        self._open: Dict[str, float] = {}

    def emit(self, step: str, status: str, label: str = "", **detail: Any) -> None:
        t_ms = int((time.perf_counter() - self.t0) * 1000)
        ev = {"step": {"id": step, "status": status, "label": label, "t_ms": t_ms, "detail": detail}}
        self.events.append(ev)
        if self.q is not None:
            self.q.put(ev)
        try:
            from VENUAI import tracing
            tracing.emit_step(step, status, label, **detail)
        except Exception:  # noqa: BLE001
            pass


@dataclass
class TurnCtx:
    question: str
    user: Any                       # AuthenticatedUser
    session_id: str = "default-session"
    top_k: int = 4
    history: List[dict] = field(default_factory=list)
    allowed_doc_ids: Optional[List[str]] = None     # already intersected with requested doc_ids
    depth: str = "standard"
    mode: str = "rag"
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    sink: StepSink = field(default_factory=StepSink)
    meter: Optional[_usage.UsageMeter] = None
    t0: float = field(default_factory=time.perf_counter)
    memories: List[dict] = field(default_factory=list)
    downshift: bool = False
    cache_hit: bool = False
    model_path: List[str] = field(default_factory=list)
    plan: Optional[model_cascade.CascadePlan] = None
    escalations: int = 0
    corrective_actions: List[str] = field(default_factory=list)

    def note_model(self, model_id: Optional[str]) -> None:
        if model_id and (not self.model_path or self.model_path[-1] != model_id):
            self.model_path.append(model_id)


def effective_allowed(visible: Optional[List[str]], requested: Optional[List[str]]) -> Optional[List[str]]:
    """Intersect a client-requested doc scope with the caller's authorised doc ids.
    Never widens access: ids the user cannot see are dropped; an empty result retrieves nothing."""
    if not requested:
        return visible
    if visible is None:
        return list(dict.fromkeys(requested))
    vis = set(visible)
    return [d for d in dict.fromkeys(requested) if d in vis]


def normalise_history(history: Optional[List[dict]]) -> List[dict]:
    """Accept both {role, content} and the UI's {id, sender, text, timestamp} shapes."""
    out: List[dict] = []
    for h in history or []:
        if not isinstance(h, dict):
            continue
        role = h.get("role") or ("user" if h.get("sender") == "user" else
                                 "assistant" if h.get("sender") in ("ai", "assistant", "bot") else None)
        content = h.get("content") if h.get("content") is not None else h.get("text")
        if role and isinstance(content, str) and content.strip():
            out.append({"role": role, "content": content, "text": content,
                        "sender": h.get("sender") or ("user" if role == "user" else "ai")})
    return out


def _call_mm(fn: Callable, *args, tenant_id: str, **kw):
    """B4 keys working memory by (tenant, user, session); older managers take no tenant_id."""
    try:
        if "tenant_id" in inspect.signature(fn).parameters:
            return fn(*args, tenant_id=tenant_id, **kw)
    except (TypeError, ValueError):
        pass
    return fn(*args, **kw)


# ── begin ───────────────────────────────────────────────────────────────────────

class BudgetRefused(RuntimeError):
    pass


def begin_turn(question: str, user, *, session_id: str, top_k: int, history: Optional[List[dict]],
               visible_doc_ids: Optional[List[str]], requested_doc_ids: Optional[List[str]],
               depth: str, mode: str, trace_id: Optional[str] = None,
               sink: Optional[StepSink] = None) -> TurnCtx:
    status = token_budget.tenant_status(user.tenant_id)
    if status == "exhausted":
        raise BudgetRefused(token_budget.EXHAUSTED_MESSAGE)
    ctx = TurnCtx(question=question, user=user, session_id=session_id, top_k=top_k,
                  history=normalise_history(history), mode=mode, depth=depth or "standard",
                  allowed_doc_ids=effective_allowed(visible_doc_ids, requested_doc_ids))
    if trace_id:
        ctx.trace_id = trace_id
    if sink is not None:
        ctx.sink = sink
    ctx.meter = _usage.start_meter(token_budget.request_ceiling() or None)
    ctx.downshift = status == "downshift"
    try:
        from VENUAI import tracing
        tracing.start_trace(ctx.trace_id, user.tenant_id, user.id, f"/query:{mode}")
    except Exception:  # noqa: BLE001
        pass
    # long-term memory (B4): recalled before generation, never required
    try:
        from VENUAI import memory_manager
        if hasattr(memory_manager, "recall"):
            recalled = memory_manager.recall(user.tenant_id, user.id, question, k=5) or []
            ctx.memories = [{"id": m.get("id"), "text": m.get("text")} for m in recalled if m.get("text")]
    except Exception as err:  # noqa: BLE001
        logger.warning(f"[turn] memory recall skipped: {err}")
    return ctx


def model_config_hash() -> str:
    from VENUAI.ai_gateway.model_router import TaskType, route
    chain = ",".join(m.model_id for m in route(TaskType.GENERATION))
    return hashlib.sha256(f"{chain}|{model_cascade.enabled()}".encode()).hexdigest()[:12]


def _cache_scope(ctx: TurnCtx) -> str:
    from VENUAI import retrieval
    return semantic_cache.scope_key(ctx.user.tenant_id, ctx.allowed_doc_ids,
                                    retrieval.get_corpus_version(), model_config_hash())


def cacheable_request(ctx: TurnCtx) -> bool:
    # context-dependent turns (history / memories) must never share answers
    return ctx.mode == "rag" and not ctx.history and not ctx.memories


def _embed_fn():
    from VENUAI import converge
    return converge.default_embed


def cache_lookup(ctx: TurnCtx) -> Optional[dict]:
    if not cacheable_request(ctx) or semantic_cache.mode() == "off":
        return None
    ctx.sink.emit("cache", "start", "Checking answer cache")
    hit = semantic_cache.get(ctx.user.tenant_id, _cache_scope(ctx), ctx.question, embed=_embed_fn())
    ctx.sink.emit("cache", "end", "Cache hit" if hit else "Cache miss", hit=bool(hit),
                  tier=hit["tier"] if hit else None)
    if not hit:
        return None
    ctx.cache_hit = True
    payload = dict(hit["payload"])
    payload["cache_hit"] = True
    payload["cache_tier"] = hit["tier"]
    return payload


# ── corrective stage ────────────────────────────────────────────────────────────

def corrective_stage(ctx: TurnCtx, deps, state: dict, *, regenerate_fn: Callable[[dict, Optional[List[Document]], List[str], model_cascade.CascadePlan], dict],
                     re_retrieve_fn: Callable[[List[str]], Optional[List[Document]]]):
    """Run the capped loop over a first-pass state. regenerate_fn(prev_state, new_docs, feedback, plan)."""
    plan = ctx.plan or model_cascade.CascadePlan(model_cascade.COMPLEX, None, None)
    enabled = corrective_rag.enabled_for(ctx.depth)
    block_regen = ctx.downshift or (ctx.meter is not None and token_budget.meter_downshift(ctx.meter))

    def extra_trigger(st: dict) -> bool:
        return model_cascade.should_escalate(plan, st, ctx.escalations, block_regen)

    def regen(new_docs, feedback, iteration):
        nonlocal plan
        cur_state = regen.cur
        use = plan
        if model_cascade.should_escalate(plan, cur_state, ctx.escalations, block_regen):
            use = model_cascade.escalation_plan()
            ctx.escalations += 1
            ctx.sink.emit("cascade", "start", "Escalating to the larger model",
                          to=use.prefer_model, reason="low verifier confidence")
        elif plan.prefer_model and not block_regen and plan.complexity == model_cascade.SIMPLE:
            use = model_cascade.CascadePlan(model_cascade.COMPLEX, None, None, "corrective regenerate")
        nxt = regenerate_fn(cur_state, new_docs, feedback, use)
        regen.cur = nxt
        plan = use
        return nxt

    regen.cur = state
    if not enabled:
        return state, corrective_rag.CorrectiveResult()
    best, res = corrective_rag.run_corrective(
        state, regenerate=regen, re_retrieve=re_retrieve_fn, emit=ctx.sink.emit,
        extra_trigger=extra_trigger,
        iterations_allowed=0 if (ctx.meter is not None and ctx.meter.ceiling and
                                 ctx.meter.total_tokens >= ctx.meter.ceiling) else None,
    )
    if corrective_rag.ACTION_ABSTAIN in res.actions:
        best = dict(best)
        best.update({
            "answer": deps.ABSTAIN_ANSWER, "annotated_answer": deps.ABSTAIN_ANSWER, "citations": [],
            "verified": False, "verification_reason": "corrective: no claim supported after re-checking",
            "insufficient_context": True, "followups": [],
        })
    return best, res


# ── response assembly ───────────────────────────────────────────────────────────

def _chunk_id(d: Document) -> str:
    m = d.metadata or {}
    h = hashlib.md5(d.page_content.encode("utf-8", "ignore")).hexdigest()[:8]
    return str(m.get("chunk_id") or f"{m.get('doc_id')}:{m.get('page')}:{h}")


def retrieved_chunks(docs: List[Document]) -> List[dict]:
    out = []
    for d in docs or []:
        m = d.metadata or {}
        score = m.get("rerank_score", m.get("retrieval_score"))
        out.append({
            "id": _chunk_id(d), "doc": m.get("filename") or m.get("source") or "Unknown",
            "doc_id": m.get("doc_id"), "page": (m["page"] + 1) if isinstance(m.get("page"), int) else m.get("page"),
            "text": (d.page_content or "")[:1200],
            "score": None if score is None else round(float(score), 4),
            "retriever": m.get("retriever") or "hybrid",
        })
    return out


def find_contradictions(answer: str, docs: List[Document]) -> List[dict]:
    try:
        from VENUAI import contradiction_agent
        if hasattr(contradiction_agent, "find_contradictions"):
            res = contradiction_agent.find_contradictions(answer, [d.page_content for d in docs])
            return [{k: c.get(k) for k in ("claim_a", "claim_b", "doc_a", "doc_b")} for c in res or []]
    except Exception as err:  # noqa: BLE001
        logger.warning(f"[turn] contradiction check skipped: {err}")
    return []


def turn_extras(ctx: TurnCtx, docs: List[Document], answer: str, followups: Optional[List[str]],
                cr: Optional[corrective_rag.CorrectiveResult], *, contradictions: Optional[List[dict]] = None) -> dict:
    m = ctx.meter
    usage = m.usage_dict() if m is not None and m.calls else {"prompt_tokens": None, "completion_tokens": None,
                                                              "total_tokens": None}
    if m is not None and m.calls and not m.complete:
        usage = {**usage, "complete": False}     # lower bound: some call did not report tokens
    return {
        "retrieved_chunks": retrieved_chunks(docs),
        "usage": usage,
        "ttft_ms": int(m.ttft_ms) if m is not None and m.ttft_ms is not None else None,
        "latency_ms": int((time.perf_counter() - ctx.t0) * 1000),
        "cache_hit": ctx.cache_hit,
        "model_path": list(ctx.model_path),
        "followups": list(followups or [])[:3],
        "contradictions": contradictions if contradictions is not None else [],
        "corrective": (cr.to_dict() if cr is not None else {"iterations": 0, "actions": []}),
        "trace_id": ctx.trace_id,
        "memories_used": list(ctx.memories),
        "pending_approval": None,
    }


def _cache_payload(result: dict) -> dict:
    drop = {"cache_hit", "latency_ms", "ttft_ms", "usage", "trace_id", "model_path", "cache_tier"}
    out = {k: v for k, v in result.items() if k not in drop}
    out["model_path_original"] = list(result.get("model_path") or [])
    return out


def finish_turn(ctx: TurnCtx, deps, state: dict, cr: corrective_rag.CorrectiveResult) -> dict:
    """Common tail for the document-RAG producers. Returns plain-dict response fields
    (state fields + contract extras). Never raises for side-effect failures."""
    docs = state.get("prompt_docs") or state.get("documents") or []
    answer = state.get("answer", "")
    contradictions = []
    if docs and not state.get("blocked") and state.get("answer_basis") == "uploaded_documents":
        contradictions = find_contradictions(answer, docs)
    fups = state.get("followups") or []
    extras = turn_extras(ctx, docs, answer, fups, cr, contradictions=contradictions)
    result = {**state, **extras}
    _account_usage(ctx)
    # cache: only verified, unblocked, document-grounded answers for context-free requests
    if (cacheable_request(ctx) and result.get("verified") and not result.get("blocked")
            and not result.get("insufficient_context") and result.get("answer_basis") == "uploaded_documents"
            and not ctx.cache_hit):
        try:
            semantic_cache.put(ctx.user.tenant_id, _cache_scope(ctx), ctx.question,
                               _cache_payload(_jsonable(result)), embed=_embed_fn())
        except Exception as err:  # noqa: BLE001
            logger.warning(f"[turn] cache write skipped: {err}")
    _remember(ctx, answer)
    _finish_trace(ctx, result)
    return result


def _jsonable(result: dict) -> dict:
    """Strip non-serialisable pieces (Documents, pydantic sources) before caching."""
    out = {}
    for k, v in result.items():
        if k in ("documents", "prompt_docs", "working_memory", "semantic_memories", "history"):
            continue
        if k == "sources":
            v = [s.model_dump() if hasattr(s, "model_dump") else s for s in v]
        out[k] = v
    return out


def _account_usage(ctx: TurnCtx) -> None:
    try:
        if ctx.meter is not None:
            token_budget.add_usage(ctx.user.tenant_id, ctx.meter.total_tokens)
    except Exception as err:  # noqa: BLE001
        logger.warning(f"[turn] token accounting skipped: {err}")


def _remember(ctx: TurnCtx, answer: str) -> None:
    try:
        from VENUAI import memory_manager
        if hasattr(memory_manager, "remember_turn") and answer:
            memory_manager.remember_turn(ctx.user.tenant_id, ctx.user.id, ctx.session_id, ctx.question, answer)
    except Exception as err:  # noqa: BLE001
        logger.warning(f"[turn] remember_turn skipped: {err}")


def _finish_trace(ctx: TurnCtx, result: dict) -> None:
    try:
        from VENUAI import tracing
        tracing.finish_trace({
            "trace_id": ctx.trace_id, "model_path": ctx.model_path, "cache_hit": ctx.cache_hit,
            "verified": result.get("verified"), "grounding_score": result.get("grounding_score"),
            "corrective": result.get("corrective"), "latency_ms": result.get("latency_ms"),
            "usage": result.get("usage"),
            "question": ctx.question,
            "answer": result.get("answer"),
            "context_chunks": [c.get("text") if isinstance(c, dict) else str(c)
                               for c in (result.get("retrieved_chunks") or [])
                               if (c.get("text") if isinstance(c, dict) else c)][:8] or None,
        })
    except Exception:  # noqa: BLE001
        pass


def finish_agent_turn(ctx: TurnCtx, deps, *, answer: str, docs: List[Document], agent_label: str) -> dict:
    """ReAct / multi-agent tail: light verification + output safety (via the same helpers the
    RAG path uses) and the contract extras. Returns {answer, ver, safe, extras}."""
    from VENUAI import answer_pipeline as ap
    ctx.sink.emit("verifying", "start", "Verifying answer")
    ver = ap.run_verification(answer, ctx.question, docs, light=True)
    final = ver.pop("final_answer")
    safe = ap.apply_output_safety(final, ver.get("grounding_score"), user_id=ctx.user.id)
    ctx.sink.emit("verifying", "end", "", verified=bool(ver.get("verified")) and not safe["blocked"])
    _account_usage(ctx)
    _remember(ctx, safe["answer"])
    extras = turn_extras(ctx, docs, safe["answer"], [], None)
    _finish_trace(ctx, {**ver, **extras})
    return {"answer": safe["answer"], "ver": ver, "safe": safe, "extras": extras}
