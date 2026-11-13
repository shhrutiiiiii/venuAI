"""
Shared post-generation steps for every answer path (standard RAG, KB fallback,
streaming, ReAct, multi-agent):

    split_citations()      strip the <citations> block and verify quotes
    run_verification()     real verification, FAIL-CLOSED (never a silent pass, never a fake score)
    apply_output_safety()  GuardrailEngine.validate_output + safeguard policy classifier

No numbers are invented here: anything not measured is None, verified=False and a reason.
"""
from __future__ import annotations

import logging
import os
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


def run_verification(answer: str, question: str, docs: List[Document], light: bool = False) -> dict:
    """
    Verify `answer` against `docs`. Returns the verify-state fields plus `final_answer`.
    Any verifier error -> verified=False + reason (fail-closed; the caller still answers,
    but the response says it is unverified). light=True skips LLM calls and claim pipeline
    (token-overlap grounding + hallucination detector only) for agent paths.
    """
    out = {"final_answer": answer}
    if not docs:
        out.update(unverified_fields("no source documents to verify against"))
        return out
    try:
        from VENUAI.verifier_agent import get_verifier
        from VENUAI.hallucination_detector import get_hallucination_detector
        verifier = get_verifier(_groq_client())
        final_answer = answer
        flags: List[str] = []
        if light:
            grounding = verifier.check_grounding(answer, docs)
            flags = [f.value for f in verifier.check_policy(answer)]
            consistency_ok = True
        else:
            v = verifier.verify(answer, question, docs)
            grounding = v.grounding_score
            flags = [f.value for f in v.policy_flags]
            consistency_ok = v.consistency_checked
            if v.revised_answer:
                final_answer = v.revised_answer
        annotated, hall_score, _ = get_hallucination_detector().analyze(final_answer, docs)
        out.update({
            "final_answer": final_answer, "annotated_answer": annotated,
            "grounding_score": grounding, "hallucination_score": hall_score,
            "verification_flags": flags,
        })
        reasons: List[str] = []
        if light:
            out.update({"evidence_coverage": None, "unsupported_claim_count": None,
                        "contradicted_claim_count": None, "critique_passed": False, "evidence_claims": []})
            reasons.append("claim-level verification not run for this mode")
        else:
            from VENUAI.verification_pipeline import get_verification_pipeline
            from VENUAI import evidence_store
            pr = get_verification_pipeline().run(question, final_answer, docs)
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
        if grounding < VERIFIED_MIN_GROUNDING:
            reasons.append(f"grounding {grounding:.2f} below {VERIFIED_MIN_GROUNDING}")
        out["verified"] = not reasons
        out["verification_reason"] = "; ".join(reasons) if reasons else None
    except Exception as e:  # noqa: BLE001 - fail closed, never 500, never silent pass
        logger.error(f"[verify] verification failed: {type(e).__name__}: {e}")
        out.update(unverified_fields(f"verification error: {type(e).__name__}"))
        out["final_answer"] = answer
    return out


def apply_output_safety(answer: str, grounding_score: Optional[float], user_id: str = "user") -> dict:
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
    verdict = sc.safeguard_classify(answer)
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
