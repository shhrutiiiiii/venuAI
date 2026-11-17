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

from VENUAI.ai_gateway.model_registry import ModelCapability, ModelEntry, get_catalog


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

# Per-task reasoning effort for models that support it (gpt-oss family).
# Classification must be cheap; generation/synthesis/verification get a middle
# setting. Reasoning tokens count against max_tokens — see provider headroom.
_TASK_EFFORT = {
    TaskType.FAST_CLASSIFICATION: "low",
    TaskType.GENERATION: "medium",
    TaskType.SYNTHESIS: "medium",
    TaskType.VERIFICATION: "medium",
}


def reasoning_effort_for(task_type: TaskType) -> str:
    return _TASK_EFFORT[task_type]


def route(task_type: TaskType) -> List[ModelEntry]:
    """Returns an ordered fallback chain of model entries for this task type.

    Order: GROQ_MODEL override (if it names an enabled chat model), then models
    with the task's capability (fast->slow, catalog order breaks ties so the
    registry's first entry is the primary), then every other enabled chat model
    as a last-resort fallback. Safety / prompt-guard classifiers are never in
    the chain."""
    required_capability = _TASK_CAPABILITY[task_type]
    enabled_chat = [m for m in get_catalog() if m.enabled and m.is_chat]
    primary = sorted(
        [m for m in enabled_chat if required_capability in m.capabilities],
        key=lambda m: _LATENCY_ORDER.get(m.latency_class, 3),
    )
    rest = [m for m in enabled_chat if m not in primary]
    candidates = primary + rest

    override = os.getenv("GROQ_MODEL")
    if override:
        overridden = [m for m in candidates if m.model_id == override]
        others = [m for m in candidates if m.model_id != override]
        candidates = overridden + others

    return candidates
