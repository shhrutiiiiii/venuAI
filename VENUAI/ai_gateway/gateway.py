"""
AIGateway — the single call surface for the raw-Groq-SDK call sites that
previously each implemented their own ad hoc fallback logic:
  - VENUAI/rag_server.py's create_groq_completion()
  - VENUAI/agents/orchestrator.py's classify_intent() and merge_results()
  - VENUAI/agents/base_agent.py's BaseSpecialistAgent.query()

What it adds that those three duplicated, inconsistent versions didn't all
have: a shared model registry/router (one place to fix a bad model id, not
three), bounded retries with backoff+jitter, a real circuit breaker so a
failing model stops being tried for a cooldown window instead of being
retried on every single request, and a telemetry log for every call
(Documents/AI/model-routing.md / observability, spec section 12).

Does NOT cover the LangChain/LangGraph call paths (generate_node, ReAct
agent, /query/stream) — see VENUAI/ai_gateway/__init__.py docstring for why.
"""

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from VENUAI.ai_gateway.model_registry import ModelEntry
from VENUAI.ai_gateway.model_router import TaskType, route
from VENUAI.ai_gateway.provider import CompletionResult, GroqProvider, ModelProvider
from VENUAI.ai_gateway import telemetry

logger = logging.getLogger("drdo-rag")

MAX_RETRIES_PER_MODEL = 2
BASE_BACKOFF_SECONDS = 0.5
CIRCUIT_BREAKER_FAILURE_THRESHOLD = 3
CIRCUIT_BREAKER_COOLDOWN_SECONDS = 60


class AIServiceUnavailableError(RuntimeError):
    """Raised when every model in the fallback chain failed. Callers must
    surface this as a controlled error — never catch it and fabricate an
    answer. See spec section 13 / 52."""


@dataclass
class _CircuitState:
    consecutive_failures: int = 0
    opened_until: float = 0.0


class _CircuitBreaker:
    """Per-process, per-model-id circuit breaker. Intentionally simple
    in-memory state (not persisted) — a process restart resets it, which is
    an acceptable Phase 1 tradeoff documented in Documents/AI/model-routing.md."""

    def __init__(self) -> None:
        self._state: Dict[str, _CircuitState] = {}

    def is_open(self, model_id: str) -> bool:
        s = self._state.get(model_id)
        return bool(s and time.time() < s.opened_until)

    def record_failure(self, model_id: str) -> None:
        s = self._state.setdefault(model_id, _CircuitState())
        s.consecutive_failures += 1
        if s.consecutive_failures >= CIRCUIT_BREAKER_FAILURE_THRESHOLD:
            s.opened_until = time.time() + CIRCUIT_BREAKER_COOLDOWN_SECONDS
            logger.warning(f"[AIGateway] Circuit opened for model '{model_id}' for {CIRCUIT_BREAKER_COOLDOWN_SECONDS}s")

    def record_success(self, model_id: str) -> None:
        if model_id in self._state:
            self._state[model_id] = _CircuitState()


class AIGateway:
    def __init__(self, groq_client) -> None:
        self._providers: Dict[str, ModelProvider] = {
            "groq": GroqProvider(groq_client),
        }
        self._breaker = _CircuitBreaker()

    def complete(
        self,
        task_type: TaskType,
        messages: List[dict],
        max_tokens: int = 1200,
        temperature: float = 0.2,
    ) -> CompletionResult:
        """
        Routes `task_type` to an ordered fallback chain of models, tries each
        (with bounded retries + backoff), skips any model whose circuit is
        open, records telemetry for the whole call, and raises
        AIServiceUnavailableError if every model in the chain fails.
        """
        chain = route(task_type)
        if not chain:
            telemetry.record(telemetry.RoutingLogEntry(
                task_type=task_type.value, requested_chain="", model_used=None,
                provider=None, fallback_count=0, latency_ms=None,
                input_tokens=None, output_tokens=None, success=False,
                error="No enabled models for this task type.",
            ))
            raise AIServiceUnavailableError("No enabled models are configured for this task type.")

        requested_chain = ",".join(m.model_id for m in chain)
        t_start = time.time()
        last_error: Optional[Exception] = None
        fallback_count = 0

        for idx, model in enumerate(chain):
            if self._breaker.is_open(model.model_id):
                logger.info(f"[AIGateway] Skipping '{model.model_id}' — circuit open.")
                fallback_count += 1 if idx > 0 else 0
                continue

            provider = self._providers.get(model.provider)
            if provider is None:
                last_error = RuntimeError(f"No provider configured for '{model.provider}'.")
                continue

            for attempt in range(MAX_RETRIES_PER_MODEL):
                try:
                    result = provider.complete(model.model_id, messages, max_tokens, temperature)
                    self._breaker.record_success(model.model_id)
                    latency_ms = (time.time() - t_start) * 1000
                    telemetry.record(telemetry.RoutingLogEntry(
                        task_type=task_type.value, requested_chain=requested_chain,
                        model_used=model.model_id, provider=model.provider,
                        fallback_count=fallback_count, latency_ms=latency_ms,
                        input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                        success=True,
                    ))
                    return result
                except Exception as e:  # noqa: BLE001 — provider SDK exceptions are not a stable type to narrow
                    last_error = e
                    self._breaker.record_failure(model.model_id)
                    logger.warning(
                        f"[AIGateway] '{model.model_id}' attempt {attempt + 1}/{MAX_RETRIES_PER_MODEL} failed: {e}"
                    )
                    if attempt < MAX_RETRIES_PER_MODEL - 1:
                        backoff = BASE_BACKOFF_SECONDS * (2 ** attempt) + random.uniform(0, 0.25)
                        time.sleep(backoff)

            fallback_count += 1

        latency_ms = (time.time() - t_start) * 1000
        telemetry.record(telemetry.RoutingLogEntry(
            task_type=task_type.value, requested_chain=requested_chain, model_used=None,
            provider=None, fallback_count=fallback_count, latency_ms=latency_ms,
            input_tokens=None, output_tokens=None, success=False,
            error=str(last_error) if last_error else "unknown",
        ))
        raise AIServiceUnavailableError(
            f"All models in the fallback chain failed for task '{task_type.value}': {last_error}"
        )


# ── Module-level singleton (mirrors the pattern already used by
# memory_manager/guardrails/verifier/analytics get_x() singletons) ──────────
_gateway: Optional[AIGateway] = None


def get_gateway(groq_client) -> AIGateway:
    global _gateway
    if _gateway is None:
        _gateway = AIGateway(groq_client)
    return _gateway


def reset_gateway_singleton() -> None:
    """
    Test-only hook. Production code always constructs one real groq_client
    and passes the same instance to get_gateway() on every call, so the
    singleton caching the first client is correct in production — but tests
    that inject different fake clients per test need a way to clear it
    between tests rather than silently reusing a previous test's client.
    """
    global _gateway
    _gateway = None
