"""
Model Router — picks which model(s), in order, should handle one request.

Routing policy (Phase 1, real but intentionally simple — see
Documents/AI/model-routing.md for what's deferred to a later phase):

  1. If GROQ_MODEL env var is set and names an enabled model in the registry,
     it is always tried first, regardless of task type — this preserves the
     existing, documented override behavior (ADR-001) rather than silently
     replacing it with "smarter" routing the operator didn't ask for.
  2. Otherwise, the task type maps to a required ModelCapability, and the
     router returns every enabled model with that capability, ordered by
     latency_class (fast first) as a simple, legible tie-break.
  3. The full ordered list is the fallback chain — AIGateway tries them in
     order and records which one actually answered.

This is deliberately not "intelligent" routing by cost/historical failure
rate yet (section 12 of the spec asks for that) — this phase ships the real
mechanism (registry + router + fallback chain + telemetry) that smarter
policy will plug into later, rather than faking a learned policy with no
data behind it.
"""

import os
from enum import Enum
from typing import List

from VENUAI.ai_gateway.model_registry import ModelCapability, ModelEntry, get_models_with_capability


class TaskType(str, Enum):
    FAST_CLASSIFICATION = "fast_classification"   # e.g. intent routing
    GENERATION = "generation"                      # standard answer generation
    SYNTHESIS = "synthesis"                         # multi-agent merge
    VERIFICATION = "verification"                   # grounding/consistency checks that use an LLM


_TASK_CAPABILITY = {
    TaskType.FAST_CLASSIFICATION: ModelCapability.FAST,
    TaskType.GENERATION: ModelCapability.REASONING,
    TaskType.SYNTHESIS: ModelCapability.REASONING,
    TaskType.VERIFICATION: ModelCapability.REASONING,
}

_LATENCY_ORDER = {"fast": 0, "standard": 1, "slow": 2, "unknown": 3}


def route(task_type: TaskType) -> List[ModelEntry]:
    """Returns an ordered fallback chain of model ids for this task type."""
    required_capability = _TASK_CAPABILITY[task_type]
    candidates = get_models_with_capability(required_capability, enabled_only=True)
    candidates = sorted(candidates, key=lambda m: _LATENCY_ORDER.get(m.latency_class, 3))

    override = os.getenv("GROQ_MODEL")
    if override:
        overridden = [m for m in candidates if m.model_id == override]
        rest = [m for m in candidates if m.model_id != override]
        candidates = overridden + rest

    if not candidates:
        # Nothing with the right capability is enabled — fall back to any
        # enabled model at all rather than returning an empty chain, so a
        # misconfigured capability tag doesn't take the whole system down.
        candidates = get_models_with_capability(ModelCapability.FAST, enabled_only=True) or \
                     get_models_with_capability(ModelCapability.REASONING, enabled_only=True)

    return candidates
