"""
Corrective / self-reflective RAG loop (capped).

    retrieve -> grade -> generate -> verify
        grade borderline  -> (LLM grader, borderline only) -> re-retrieve with a better query
        verify unhappy    -> iteration 1: re_retrieve using the unsupported claims as extra
                             queries, regenerate on the new passages
                          -> iteration 2: regenerate with critic feedback (escalated model)
        still bad         -> keep the better attempt; abstain honestly only when NO claim is
                             supported at all.

Hard cap CORRECTIVE_MAX_ITERS (default 2); converge.has_converged stops early when a new
answer is the same as the previous one. The loop itself is pure: all I/O (retrieve, generate,
verify) comes in through callables so it is unit-testable with fakes and shared by
/query and /query/stream. Default ON for depth != "fast".
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from VENUAI import converge

ACTION_RE_RETRIEVE, ACTION_REGENERATE, ACTION_ABSTAIN = "re_retrieve", "regenerate", "abstain"


def max_iters() -> int:
    try:
        return max(0, min(2, int(os.getenv("CORRECTIVE_MAX_ITERS", "2"))))
    except ValueError:
        return 2


def enabled_for(depth: Optional[str]) -> bool:
    if os.getenv("CORRECTIVE_RAG", "on").strip().lower() in ("off", "0", "false"):
        return False
    return (depth or "standard").lower() != "fast"


@dataclass
class CorrectiveResult:
    iterations: int = 0
    actions: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"iterations": self.iterations, "actions": list(self.actions)}


# ── grading (cheap first) ──────────────────────────────────────────────────────

def grade_retrieval(scores: List[dict], good_at: float = 0.15) -> str:
    """'good' | 'borderline' from rerank scores already computed by retrieval (no LLM).
    Borderline = survived the relevance floor but even the best passage is weak."""
    vals = [s.get("rerank_score") for s in scores or [] if s.get("rerank_score") is not None]
    if not vals:
        return "good"          # no rerank signal (similarity/fusion path): cannot judge, do not loop
    return "good" if max(vals) >= good_at else "borderline"


GRADER_SCHEMA = {"type": "object", "properties": {
    "relevant": {"type": "boolean"}, "better_query": {"type": "string"}}, "required": ["relevant"]}


def llm_grade(question: str, docs) -> Optional[dict]:
    """Borderline-only grader on the fast model. None when unavailable/disabled."""
    if os.getenv("CORRECTIVE_LLM_GRADER", "on").strip().lower() in ("off", "0", "false"):
        return None
    from VENUAI.ai_gateway.model_router import TaskType
    from VENUAI.model_cascade import fast_model_id
    from VENUAI.structured import complete_json
    ctx = "\n---\n".join(d.page_content[:300] for d in docs[:3])
    prompt = (f"Question: {question}\n\nPassages:\n{ctx}\n\nDo these passages contain what is needed to "
              'answer the question? Reply JSON {"relevant": true|false, "better_query": "<a better '
              'search query if not relevant, else empty>"}')
    data = complete_json(TaskType.FAST_CLASSIFICATION, [{"role": "user", "content": prompt}],
                         "retrieval_grade", GRADER_SCHEMA, max_tokens=120, prefer_model=fast_model_id())
    return data if isinstance(data, dict) else None


# ── verification-driven correction ─────────────────────────────────────────────

def needs_correction(state: dict) -> bool:
    """Measured evidence says the answer is poorly supported. The deterministic token-overlap
    critic alone is too noisy on short answers, so it must be corroborated by the NLI
    hallucination score; a contradicted claim is sufficient on its own."""
    if state.get("blocked") or state.get("answer_basis") not in (None, "uploaded_documents"):
        return False
    if state.get("insufficient_context"):
        return False
    if (state.get("contradicted_claim_count") or 0) > 0:
        return True
    h = state.get("hallucination_score")
    return (state.get("critique_passed") is False and (state.get("unsupported_claim_count") or 0) > 0
            and h is not None and h >= 0.5)


def unsupported_claims(state: dict) -> List[str]:
    out = []
    for c in state.get("evidence_claims") or []:
        st = str(c.get("support_status", "")).lower()
        if st.endswith("unsupported") or st.endswith("contradicted"):
            out.append(str(c.get("text", ""))[:200])
    return [t for t in out if t]


def quality_key(state: dict):
    """Higher is better: critic passed, fewer contradicted/unsupported, lower hallucination."""
    return (bool(state.get("critique_passed")),
            -(state.get("contradicted_claim_count") or 0),
            -(state.get("unsupported_claim_count") or 0),
            -(state.get("hallucination_score") if state.get("hallucination_score") is not None else 1.0))


def nothing_supported(state: dict) -> bool:
    claims = state.get("evidence_claims") or []
    return bool(claims) and (state.get("evidence_coverage") or 0.0) == 0.0


def run_corrective(
    initial: dict, *,
    regenerate: Callable[[Optional[List], List[str], int], dict],
    re_retrieve: Callable[[List[str]], Optional[List]],
    emit: Callable[..., None] = lambda *a, **k: None,
    iterations_allowed: Optional[int] = None,
    allow_regeneration: bool = True,
    embed=None,
    extra_trigger: Callable[[dict], bool] = lambda s: False,
):
    """
    initial: state dict from the first attempt (answer, verification fields, documents...).
    regenerate(docs_or_None, feedback_claims, iteration) -> new full state dict.
    re_retrieve(extra_queries) -> new docs list or None when nothing new was found.
    Returns (best_state, CorrectiveResult).
    """
    res = CorrectiveResult()
    cap = max_iters() if iterations_allowed is None else min(iterations_allowed, max_iters())
    best = cur = initial
    re_retrieved = False
    while res.iterations < cap and allow_regeneration and (needs_correction(cur) or extra_trigger(cur)):
        res.iterations += 1
        feedback = unsupported_claims(cur)
        new_docs = None
        emit("correcting", "start", f"Re-checking answer (pass {res.iterations})")
        if not re_retrieved:
            re_retrieved = True
            new_docs = re_retrieve(feedback)
            if new_docs:
                res.actions.append(ACTION_RE_RETRIEVE)
        res.actions.append(ACTION_REGENERATE)
        nxt = regenerate(new_docs, feedback, res.iterations)
        emit("correcting", "end", "", iteration=res.iterations)
        same = converge.has_converged(cur.get("answer", ""), nxt.get("answer", ""), embed=embed)
        if quality_key(nxt) > quality_key(best):
            best = nxt
        cur = nxt
        if same:
            break
    if res.iterations and needs_correction(best) and nothing_supported(best):
        res.actions.append(ACTION_ABSTAIN)
    return best, res
