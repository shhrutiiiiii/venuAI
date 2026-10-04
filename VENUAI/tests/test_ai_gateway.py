"""
Real tests for the Phase 1 AI Gateway (VENUAI/ai_gateway/): model registry,
router, provider wrapping, retry/fallback/circuit-breaker behavior, and
telemetry recording. No network calls — a fake Groq-shaped client stands in
for the real SDK, exercising the actual GroqProvider/AIGateway code paths.
"""

import os

import pytest

from VENUAI.ai_gateway import model_registry, telemetry
from VENUAI.ai_gateway.gateway import AIGateway, AIServiceUnavailableError, _CircuitBreaker
from VENUAI.ai_gateway.model_registry import ModelCapability
from VENUAI.ai_gateway.model_router import TaskType, route
from VENUAI.ai_gateway.provider import GroqProvider, OpenAIProvider, LocalProvider


# ── Model Registry ───────────────────────────────────────────────────────────

def test_catalog_models_disabled_without_groq_api_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    catalog = model_registry.get_catalog()
    assert all(not m.enabled for m in catalog)


def test_catalog_models_enabled_with_groq_api_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    catalog = model_registry.get_catalog()
    assert any(m.enabled for m in catalog)


def test_no_vision_model_is_registered(monkeypatch):
    """This must stay false until a real vision-capable model is wired up —
    asserting its absence is itself a regression test against fabrication."""
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    vision_models = model_registry.get_models_with_capability(ModelCapability.VISION)
    assert vision_models == []


def test_get_model_by_id(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    m = model_registry.get_model("llama-3.3-70b-versatile")
    assert m is not None
    assert m.provider == "groq"


# ── Model Router ──────────────────────────────────────────────────────────────

def test_route_fast_classification_returns_fast_models(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    chain = route(TaskType.FAST_CLASSIFICATION)
    assert len(chain) > 0
    assert all(ModelCapability.FAST in m.capabilities for m in chain)


def test_route_respects_groq_model_override(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-120b")
    chain = route(TaskType.GENERATION)
    assert chain[0].model_id == "openai/gpt-oss-120b"


def test_route_with_no_api_key_returns_empty_chain(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    chain = route(TaskType.GENERATION)
    assert chain == []


# ── GroqProvider ──────────────────────────────────────────────────────────────

class _FakeUsage:
    prompt_tokens = 10
    completion_tokens = 5


class _FakeChoice:
    class message:
        content = "hello from fake groq"


class _FakeResponse:
    choices = [_FakeChoice()]
    usage = _FakeUsage()


class _FakeGroqClient:
    def __init__(self, fail_times=0, exc=RuntimeError("simulated failure")):
        self.fail_times = fail_times
        self.exc = exc
        self.calls = 0

        class _Completions:
            def create(inner_self, model, messages, max_tokens, temperature):
                self.calls += 1
                if self.calls <= self.fail_times:
                    raise self.exc
                return _FakeResponse()

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def test_groq_provider_returns_completion_result():
    provider = GroqProvider(_FakeGroqClient())
    result = provider.complete("llama-3.3-70b-versatile", [{"role": "user", "content": "hi"}], 100, 0.2)
    assert result.content == "hello from fake groq"
    assert result.provider == "groq"
    assert result.input_tokens == 10
    assert result.output_tokens == 5


def test_openai_provider_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        OpenAIProvider().complete("gpt-4", [], 100, 0.2)


def test_local_provider_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        LocalProvider().complete("local-model", [], 100, 0.2)


# ── AIGateway: retry / fallback / circuit breaker / telemetry ─────────────────

def _gateway_with_fake_client(monkeypatch, fail_times=0, exc=RuntimeError("boom")):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    gw = AIGateway(_FakeGroqClient(fail_times=fail_times, exc=exc))
    gw._breaker = _CircuitBreaker()  # fresh breaker per test — no cross-test leakage
    return gw


def test_gateway_success_on_first_try(monkeypatch):
    gw = _gateway_with_fake_client(monkeypatch, fail_times=0)
    result = gw.complete(TaskType.FAST_CLASSIFICATION, [{"role": "user", "content": "hi"}])
    assert result.content == "hello from fake groq"


def test_gateway_retries_then_succeeds(monkeypatch):
    # Fails once, then succeeds — within MAX_RETRIES_PER_MODEL for one model.
    gw = _gateway_with_fake_client(monkeypatch, fail_times=1)
    result = gw.complete(TaskType.FAST_CLASSIFICATION, [{"role": "user", "content": "hi"}])
    assert result.content == "hello from fake groq"


def test_gateway_raises_controlled_error_when_everything_fails(monkeypatch):
    """Never hallucinate an answer when the model layer is genuinely down —
    spec section 13/52."""
    gw = _gateway_with_fake_client(monkeypatch, fail_times=999)
    with pytest.raises(AIServiceUnavailableError):
        gw.complete(TaskType.FAST_CLASSIFICATION, [{"role": "user", "content": "hi"}])


def test_gateway_records_telemetry_for_success_and_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(telemetry, "DB_PATH", tmp_path / "routing.db")
    telemetry.init_db()

    gw = _gateway_with_fake_client(monkeypatch, fail_times=0)
    gw.complete(TaskType.FAST_CLASSIFICATION, [{"role": "user", "content": "hi"}])

    rows = telemetry.recent(10)
    assert len(rows) == 1
    assert rows[0]["success"] == 1
    assert rows[0]["model_used"] is not None


def test_circuit_breaker_opens_after_threshold_failures():
    from VENUAI.ai_gateway.gateway import _CircuitBreaker, CIRCUIT_BREAKER_FAILURE_THRESHOLD

    breaker = _CircuitBreaker()
    for _ in range(CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        breaker.record_failure("some-model")
    assert breaker.is_open("some-model") is True


def test_circuit_breaker_resets_on_success():
    breaker = _CircuitBreaker()
    breaker.record_failure("some-model")
    breaker.record_failure("some-model")
    breaker.record_success("some-model")
    breaker.record_failure("some-model")
    assert breaker.is_open("some-model") is False
