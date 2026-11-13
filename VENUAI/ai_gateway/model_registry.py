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
    JUDGE = "judge"                      # distinct model family — usable as an independent LLM judge for evals
    SAFETY = "safety"                    # policy-based safety classifier (gpt-oss-safeguard)
    PROMPT_GUARD = "prompt_guard"        # prompt-injection / jailbreak classifier (not a chat model)


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
    # How this model exposes reasoning on Groq: "effort" = gpt-oss family
    # (accepts reasoning_effort low/medium/high), "hidden" = qwen3 family
    # (reasoning_format="hidden" strips <think> text), "" = non-reasoning.
    reasoning_style: str = ""
    is_chat: bool = True                # False for classifiers that are not general chat/completion models


# ── The actual, real model catalog for this deployment ────────────────────────
# Context windows / output limits verified against Groq's GET /openai/v1/models
# (context_window, max_completion_tokens) at authoring. If Groq changes these, update here — this registry
# exists precisely so that correction happens in one place, not three.
_CATALOG: List[ModelEntry] = [
    ModelEntry(
        model_id="openai/gpt-oss-120b",
        provider="groq",
        capabilities=[ModelCapability.REASONING],
        context_window=131_072,
        max_output_tokens=65_536,
        cost_tier="medium",
        latency_class="standard",
        reasoning_style="effort",
        notes="Primary model (ADR-001, superseded default by ADR-009).",
    ),
    ModelEntry(
        model_id="openai/gpt-oss-20b",
        provider="groq",
        capabilities=[ModelCapability.FAST],
        context_window=131_072,
        max_output_tokens=65_536,
        cost_tier="low",
        latency_class="fast",
        reasoning_style="effort",
        notes="Fast model for classification; last-resort generation fallback.",
    ),
    ModelEntry(
        model_id="qwen/qwen3.8-27b",
        provider="groq",
        capabilities=[ModelCapability.REASONING, ModelCapability.JUDGE],
        context_window=131_072,
        max_output_tokens=16_384,
        cost_tier="medium",
        latency_class="standard",
        reasoning_style="hidden",
        notes="Different model family from gpt-oss: second-opinion fallback and independent eval judge.",
    ),
    ModelEntry(
        model_id="openai/gpt-oss-safeguard-20b",
        provider="groq",
        capabilities=[ModelCapability.SAFETY],
        context_window=131_072,
        max_output_tokens=65_536,
        cost_tier="low",
        latency_class="fast",
        reasoning_style="effort",
        is_chat=False,
        notes="Policy-based safety classifier; never used as a generation model.",
    ),
    ModelEntry(
        model_id="meta-llama/llama-prompt-guard-2-86m",
        provider="groq",
        capabilities=[ModelCapability.PROMPT_GUARD],
        context_window=512,
        max_output_tokens=512,
        cost_tier="low",
        latency_class="fast",
        is_chat=False,
        notes="Prompt-injection classifier; not a chat model.",
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
                reasoning_style=entry.reasoning_style,
                is_chat=entry.is_chat,
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
