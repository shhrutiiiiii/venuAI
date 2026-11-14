"""
Shared post-generation steps for every answer path (standard RAG, KB fallback,
streaming, ReAct, multi-agent):

    split_citations()      strip the <citations> block and verify quotes
    run_verification()     real verification, FAIL-CLOSED (never a silent pass, never a fake score)
    apply_output_safety()  GuardrailEngine.validate_output + safeguard policy classifier

No numbers are invented here: anything not measured is None, verified=False and a reason.
"""
from __future__ import annotations

import contextvars
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

from langchain_core.documents import Document

logger = logging.getLogger("drdo-rag")

VERIFIED_MIN_GROUNDING = 0.5

ANSWER_BASIS_DOCUMENTS = "uploaded_documents"
ANSWER_BASIS_KB = "builtin_knowledge_base"
ANSWER_BASIS_NONE = "none"
KB_FILENAME = "Built-in knowledge base (not from your documents)"
KB_NOTICE = "Answer based on VenuAI's built-in knowledge base, not from your documents."


def split_citations(raw_answer: str) -> Tuple[str, Dict[int, str]]:
    from VENUAI.grounded_citations import extract_citations
    return extract_citations(raw_answer)


def unverified_fields(reason: str) -> dict:
    """The honest 'we did not / could not verify' shape (never a made-up score)."""
    return {
        "grounding_score": None,
        "hallucination_score": None,
        "verification_flags": [],
        "evidence_coverage": None,
        "unsupported_claim_count": None,
        "contradicted_claim_count": None,
        "critique_passed": False,
        "evidence_claims": [],
        "verified": False,
        "verification_reason": reason,
    }


MAX_NLI_CLAIMS = int(os.getenv("VERIFY_MAX_CLAIMS", "6") or 6)
NLI_CHUNKS_PER_CLAIM = int(os.getenv("VERIFY_CHUNKS_PER_CLAIM", "2") or 2)
_TOK = re.compile(r"\b\w+\b")


def ctx_submit(pool, fn, *args, **kwargs):
    """pool.submit that carries contextvars (trace / usage meter) into the worker thread."""
    ctx = contextvars.copy_context()
    return pool.submit(ctx.run, lambda: fn(*args, **kwargs))


def fast_hallucination_analyze(answer: str, docs: List[Document]):
    """
    Same verdicts as HallucinationDetector.analyze() but cheaper: claims are capped
    (VERIFY_MAX_CLAIMS), each claim is only NLI-scored against its VERIFY_CHUNKS_PER_CLAIM
    best token-overlap chunks (instead of every chunk) and all pairs go in ONE batched
    predict call. Falls back to the detector's own token-F1 path when no NLI model loads.
    Returns (annotated_answer, hallucination_score, claims).
    """
    from VENUAI.hallucination_detector import get_hallucination_detector, Claim
    det = get_hallucination_detector()
    if not hasattr(det, "extract_claims"):   # alternate detector implementations
        return det.analyze(answer, docs)
    claim_texts = det.extract_claims(answer)[:MAX_NLI_CLAIMS]
    if not claim_texts:
        return answer, 0.0, []
    model = det._nli_model if det._load_nli_model() else None
    if model is None or not docs:
        claims = [det.verify_claim(c, docs) for c in claim_texts]
    else:
        chunks = [d.page_content[:500] for d in docs]
        chunk_toks = [set(_TOK.findall(c.lower())) for c in chunks]
        pairs, owner = [], []
        for ci, c in enumerate(claim_texts):
            ct = set(_TOK.findall(c.lower()))
            order = sorted(range(len(chunks)), key=lambda i: len(ct & chunk_toks[i]), reverse=True)
            for i in order[:NLI_CHUNKS_PER_CLAIM]:
                pairs.append((chunks[i], c))
                owner.append((ci, i))
        best = {ci: (0.0, "") for ci in range(len(claim_texts))}
        claims = None
        try:
            probs = model.predict(pairs, apply_softmax=True, batch_size=16)
            for (ci, i), pr in zip(owner, probs):
                if float(pr[0]) > best[ci][0]:
                    best[ci] = (float(pr[0]), chunks[i])
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[verify] batched NLI failed ({e}); token-F1 fallback")
            claims = [det.verify_claim(c, docs) for c in claim_texts]
        if claims is None:
            claims = []
            for ci, c in enumerate(claim_texts):
                sc, ch = best[ci]
                verdict = ("SUPPORTED" if sc > det._SUPPORTED_THRESHOLD else
                           "UNCERTAIN" if sc > det._UNCERTAIN_THRESHOLD else "UNSUPPORTED")
                claims.append(Claim(text=c, verdict=verdict,
                                    evidence=ch[:200] if verdict != "UNSUPPORTED" else "",
                                    confidence=round(sc, 3)))
    return det.annotate_answer(answer, claims), det.compute_hallucination_score(claims), claims


def warm_models() -> None:
    """Load the NLI model + reranker off the request path (called from a startup thread)."""
    try:
        from VENUAI.hallucination_detector import get_hallucination_detector
        get_hallucination_detector()._load_nli_model()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[warmup] NLI model: {e}")
    try:
        from VENUAI import reranker
        reranker.rerank_scores("warmup", ["warmup"])
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[warmup] reranker: {e}")


def run_verification(answer: str, question: str, docs: List[Document], light: bool = False,
                     with_safety: bool = False) -> dict:
    """
    Verify `answer` against `docs`. Returns the verify-state fields plus `final_answer`.
    Any verifier error -> verified=False + reason (fail-closed; the caller still answers,
    but the response says it is unverified). light=True skips the LLM consistency call and
    claim pipeline (token-overlap grounding + hallucination detector only) for agent paths.

    Latency: the independent checks (LLM consistency call, NLI claim check, claim-level
    pipeline and - when with_safety - the safeguard policy classifier) run concurrently.
    with_safety=True adds out["safeguard_verdict"] for apply_output_safety(verdict=...).
    """
    out = {"final_answer": answer}
    if not docs:
        out.update(unverified_fields("no source documents to verify against"))
        return out
    try:
        from VENUAI.verifier_agent import get_verifier
        verifier = get_verifier(_groq_client())
        reasons: List[str] = []
        pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="verify")
        try:
            f_ver = None if light else ctx_submit(pool, verifier.verify, answer, question, docs)
            f_hal = ctx_submit(pool, fast_hallucination_analyze, answer, docs)
            f_pipe = None
            if not light:
                from VENUAI.verification_pipeline import get_verification_pipeline
                f_pipe = ctx_submit(pool, get_verification_pipeline().run, question, answer, docs)
            f_safe = None
            if with_safety:
                from VENUAI import safety_classifiers as sc
                f_safe = ctx_submit(pool, sc.safeguard_classify, answer)
            final_answer = answer
            if light:
                grounding = verifier.check_grounding(answer, docs)
                flags = [f.value for f in verifier.check_policy(answer)]
                consistency_ok = True
            else:
                v = f_ver.result()
                grounding, flags = v.grounding_score, [f.value for f in v.policy_flags]
                consistency_ok = v.consistency_checked
                if v.revised_answer:
                    final_answer = v.revised_answer
            annotated, hall_score, hclaims = f_hal.result()
            if final_answer != answer and hclaims:  # re-anchor annotations on the revised text
                from VENUAI.hallucination_detector import get_hallucination_detector
                annotated = get_hallucination_detector().annotate_answer(final_answer, hclaims)
            out.update({
                "final_answer": final_answer, "annotated_answer": annotated,
                "grounding_score": grounding, "hallucination_score": hall_score,
                "verification_flags": flags,
            })
            if light:
                out.update({"evidence_coverage": None, "unsupported_claim_count": None,
                            "contradicted_claim_count": None, "critique_passed": False, "evidence_claims": []})
                reasons.append("claim-level verification not run for this mode")
            else:
                from VENUAI import evidence_store
                pr = f_pipe.result()
                out.update({
                    "evidence_coverage": pr.evidence_graph.evidence_coverage,
                    "unsupported_claim_count": pr.critique.unsupported_claim_count,
                    "contradicted_claim_count": pr.critique.contradicted_claim_count,
                    "critique_passed": pr.critique.passed,
                    "evidence_claims": evidence_store.serialize_claims(pr.evidence_graph.claims),
                })
                if not pr.critique.passed:
                    reasons.append(f"critic: {pr.critique.unsupported_claim_count} unsupported / "
                                   f"{pr.critique.contradicted_claim_count} contradicted claim(s)")
                if not consistency_ok:
                    reasons.append("answer/question consistency check unavailable")
            if f_safe is not None:
                try:
                    out["safeguard_verdict"] = f_safe.result()
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[verify] safeguard classifier error: {e}")
        finally:
            pool.shutdown(wait=False)
        if grounding < VERIFIED_MIN_GROUNDING:
            reasons.append(f"grounding {grounding:.2f} below {VERIFIED_MIN_GROUNDING}")
        out["verified"] = not reasons
        out["verification_reason"] = "; ".join(reasons) if reasons else None
    except Exception as e:  # noqa: BLE001 - fail closed, never 500, never silent pass
        logger.error(f"[verify] verification failed: {type(e).__name__}: {e}")
        out.update(unverified_fields(f"verification error: {type(e).__name__}"))
        out["final_answer"] = answer
    return out


def apply_output_safety(answer: str, grounding_score: Optional[float], user_id: str = "user",
                        verdict: Optional[dict] = None) -> dict:
    """
    Output guardrails + policy classifier. Returns
    {answer, blocked, block_reason, safety_check, safety_detail}.
    safety_check: "passed" | "flagged" | "unavailable" | "disabled"  (never fakes a pass)
    """
    from VENUAI.guardrails import get_guardrail_engine
    from VENUAI import safety_classifiers as sc

    ge = get_guardrail_engine()
    g = ge.validate_output(answer, grounding_score, user_id=user_id)
    if not g.passed:
        return {"answer": g.sanitized_input_or_output, "blocked": True,
                "block_reason": g.block_reason or g.violation_type, "safety_check": "passed",
                "safety_detail": {"stage": "output_guardrail"}}
    text = g.sanitized_input_or_output
    verdict = verdict if verdict is not None else sc.safeguard_classify(answer)
    if verdict["status"] == "flagged":
        ge.log_violation(user_id, "", "output_policy", "block", f"safeguard: {verdict.get('category')}")
        return {
            "answer": "I can't provide that response because it may conflict with defence information "
                      "handling policy. Please rephrase or consult your security officer.",
            "blocked": True, "block_reason": f"output_policy:{verdict.get('category') or 'violation'}",
            "safety_check": "flagged", "safety_detail": verdict,
        }
    return {"answer": text, "blocked": False, "block_reason": None,
            "safety_check": verdict["status"], "safety_detail": verdict}


def _groq_client():
    from groq import Groq
    return Groq(api_key=os.getenv("GROQ_API_KEY"))


def kb_document(kb_text: str) -> Document:
    """The built-in KB wrapped as a clearly-labelled document (never presented as user documents)."""
    return Document(
        page_content=f"DRDO Master Knowledge Base:\n{kb_text}",
        metadata={"filename": KB_FILENAME, "page": 0, "kb_fallback": True},
    )
