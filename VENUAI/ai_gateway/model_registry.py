"""
Model Registry — what models actually exist, and what each is good for.

Replaces the hardcoded, duplicated candidate-model lists that previously
lived independently in VENUAI/rag_server.py (`create_groq_completion`) and
VENUAI/agents/base_agent.py (`candidate_models`). Both now ask this registry
instead of hardcoding model name strings.

This registry only lists models actually reachable from this codebase today
(Groq). It does not list OpenAI/local models as "available" — that would be
fabricating infrastructure that doesn't exist. See `ModelCapability` and the
`enabled` field: a model is only enabled if the provider it needs is actually
configured (i.e., GROQ_API_KEY is set for Groq models).
"""

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class ModelCapability(str, Enum):
    FAST = "fast"
    REASONING = "reasoning"
    VISION = "vision"
    EMBEDDING = "embedding"
    RERANKER = "reranker"
    VERIFIER = "verifier"
    SPEECH_TO_TEXT = "speech_to_text"
    TEXT_TO_SPEECH = "text_to_speech"


class PrivacyClass(str, Enum):
    """See Documents/AI/model-routing.md — privacy-mode enforcement itself
    (STANDARD/PRIVATE/LOCAL_ONLY) is tracked as not-yet-implemented; this
    field exists so routing decisions can be privacy-aware once that's built,
    without a schema change later."""
    EXTERNAL = "external"   # leaves this process, goes to a third-party API
    LOCAL = "local"         # would run in-process/on-prem — none configured today


@dataclass
class ModelEntry:
    model_id: str
    provider: str                      # "groq" today; "openai"/"local" reserved, unused
    capabilities: List[ModelCapability]
    context_window: int
    max_output_tokens: int
    privacy_class: PrivacyClass = PrivacyClass.EXTERNAL
    cost_tier: str = "unknown"          # "low" | "medium" | "high" | "unknown" — no live pricing API wired up
    latency_class: str = "unknown"      # "fast" | "standard" | "slow"
    enabled: bool = True
    notes: str = ""


# ── The actual, real model catalog for this deployment ────────────────────────
# Context windows / output limits per Groq's published model cards as of this
# registry's authoring. If Groq changes these, update here — this registry
# exists precisely so that correction happens in one place, not three.
_CATALOG: List[ModelEntry] = [
    ModelEntry(
        model_id="llama-3.3-70b-versatile",
        provider="groq",
        capabilities=[ModelCapability.REASONING, ModelCapability.FAST],
        context_window=128_000,
        max_output_tokens=32_768,
        cost_tier="medium",
        latency_class="fast",
        notes="Primary default model — see ADR-001 in Documents/architecture-decisions.md.",
    ),
    ModelEntry(
        model_id="llama-3.1-8b-instant",
        provider="groq",
        capabilities=[ModelCapability.FAST],
        context_window=128_000,
        max_output_tokens=8_192,
        cost_tier="low",
        latency_class="fast",
        notes="Cheap/fast fallback and classification model.",
    ),
    ModelEntry(
        model_id="openai/gpt-oss-120b",
        provider="groq",
        capabilities=[ModelCapability.REASONING],
        context_window=131_072,
        max_output_tokens=32_768,
        cost_tier="medium",
        latency_class="standard",
        notes="Secondary fallback if llama-3.3-70b-versatile fails.",
    ),
    ModelEntry(
        model_id="openai/gpt-oss-20b",
        provider="groq",
        capabilities=[ModelCapability.FAST],
        context_window=131_072,
        max_output_tokens=32_768,
        cost_tier="low",
        latency_class="fast",
        notes="Tertiary fallback.",
    ),
]

# No vision model is configured. This is deliberately absent, not an oversight
# — Groq's text-chat endpoints used elsewhere in this codebase do not serve a
# vision-capable model today. A VISION entry must not be added here until a
# real vision-capable model + credential path exists — see
# Documents/AI/model-routing.md "Not Yet Implemented".


def get_catalog() -> List[ModelEntry]:
    """Returns the full catalog, with `enabled` reflecting live env state."""
    groq_configured = bool(os.getenv("GROQ_API_KEY"))
    catalog = []
    for entry in _CATALOG:
        live_enabled = entry.enabled and (groq_configured if entry.provider == "groq" else False)
        catalog.append(
            ModelEntry(
                model_id=entry.model_id,
                provider=entry.provider,
                capabilities=entry.capabilities,
                context_window=entry.context_window,
                max_output_tokens=entry.max_output_tokens,
                privacy_class=entry.privacy_class,
                cost_tier=entry.cost_tier,
                latency_class=entry.latency_class,
                enabled=live_enabled,
                notes=entry.notes,
            )
        )
    return catalog


def get_models_with_capability(capability: ModelCapability, enabled_only: bool = True) -> List[ModelEntry]:
    return [
        m for m in get_catalog()
        if capability in m.capabilities and (m.enabled if enabled_only else True)
    ]


def get_model(model_id: str) -> Optional[ModelEntry]:
    for m in get_catalog():
        if m.model_id == model_id:
            return m
    return None
