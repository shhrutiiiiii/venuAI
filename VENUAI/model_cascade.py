"""
Model cascade: cheap fast model for simple lookups, big model for synthesis, escalation
to the big model at high reasoning effort when the verifier is unhappy.

  MODEL_CASCADE=on|off (default on)     MAX_ESCALATIONS (default 1)
  CASCADE_LLM_ROUTER=on|off (default off) - ask the fast model only when rules are ambiguous
  ESCALATE_GROUNDING_BELOW (default 0.1) - grounding below this (when measured) escalates

Complexity is decided by rules first (length, synthesis / multi-hop cues, lookup cues);
ambiguous questions are treated as complex unless CASCADE_LLM_ROUTER=on. No fabricated
confidence: escalation uses measured verifier fields only.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

SIMPLE, COMPLEX, AMBIGUOUS = "simple", "complex", "ambiguous"

_SYNTH = re.compile(r"\b(compare|comparison|contrast|summari[sz]e|summary|explain|why|how does|how do|"
                    r"difference|differences|analy[sz]e|evaluate|trade-?off|relationship|implication|"
                    r"impact|overall|across|versus|vs\.?|pros and cons|list all|describe)\b", re.I)
_MULTIHOP = re.compile(r"\b(both|and also|as well as|which of|that also|whose .* of the)\b", re.I)
_LOOKUP = re.compile(r"^\s*(what is|what are|what was|which|who|when|where|how many|how much|how long|"
                     r"how far|how high|what's|name the|give the|state the)\b", re.I)


def enabled() -> bool:
    return os.getenv("MODEL_CASCADE", "on").strip().lower() not in ("off", "0", "false")


def max_escalations() -> int:
    try:
        return int(os.getenv("MAX_ESCALATIONS", "1"))
    except ValueError:
        return 1


def escalate_grounding_below() -> float:
    try:
        return float(os.getenv("ESCALATE_GROUNDING_BELOW", "0.1"))
    except ValueError:
        return 0.1


def classify_complexity(question: str) -> str:
    q = (question or "").strip()
    words = len(q.split())
    if _SYNTH.search(q) or words > 35 or q.count("?") > 1:
        return COMPLEX
    if _MULTIHOP.search(q):
        return COMPLEX
    if words <= 22 and _LOOKUP.search(q):
        return SIMPLE
    return AMBIGUOUS


def fast_model_id() -> Optional[str]:
    from VENUAI.ai_gateway.model_registry import ModelCapability, get_models_with_capability
    fast = [m for m in get_models_with_capability(ModelCapability.FAST) if m.is_chat]
    return fast[0].model_id if fast else None


def strong_model_id() -> Optional[str]:
    from VENUAI.ai_gateway.model_router import TaskType, route
    chain = route(TaskType.GENERATION)
    return chain[0].model_id if chain else None


@dataclass
class CascadePlan:
    complexity: str
    prefer_model: Optional[str]      # None = router default (the strong model)
    effort: Optional[str]            # None = router default
    reason: str = ""


def plan_generation(question: str, *, downshift: bool = False, llm_router=None) -> CascadePlan:
    """Pick the model for the first generation attempt."""
    fast = fast_model_id()
    if not enabled():
        return CascadePlan(COMPLEX, None, None, "cascade disabled")
    if downshift and fast:
        return CascadePlan(SIMPLE, fast, "low", "token budget downshift")
    c = classify_complexity(question)
    if c == AMBIGUOUS and llm_router is not None:
        try:
            c = SIMPLE if llm_router(question) == SIMPLE else COMPLEX
        except Exception:  # noqa: BLE001
            c = COMPLEX
    if c == SIMPLE and fast:
        return CascadePlan(SIMPLE, fast, "low", "rule: simple lookup")
    return CascadePlan(COMPLEX if c != SIMPLE else SIMPLE, None, None, "rule: synthesis/ambiguous")


def escalation_plan() -> CascadePlan:
    return CascadePlan(COMPLEX, strong_model_id(), "high", "escalated: low verifier confidence")


def verifier_unhappy(ver: dict) -> bool:
    """True when measured verifier output says the answer is poorly supported: a contradicted
    claim, critic + NLI agreeing, or a (token-overlap) grounding score near zero."""
    g = ver.get("grounding_score")
    if g is not None and g < escalate_grounding_below():
        return True
    if (ver.get("contradicted_claim_count") or 0) > 0:
        return True
    h = ver.get("hallucination_score")
    return (ver.get("critique_passed") is False and (ver.get("unsupported_claim_count") or 0) > 0
            and h is not None and h >= 0.5)


def should_escalate(plan: CascadePlan, ver: dict, escalations_used: int, downshift: bool = False) -> bool:
    if not enabled() or downshift or escalations_used >= max_escalations():
        return False
    if plan.prefer_model is None:       # already on the strong model at default effort: escalate effort only
        return verifier_unhappy(ver) and plan.effort != "high"
    return verifier_unhappy(ver)
