"""Model layer: registry contents, router ordering, streaming fallback
semantics, Groq client timeout config, reasoning-effort passthrough. All mocked."""

import asyncio

import pytest

from VENUAI.ai_gateway import gateway as gw_mod, provider, telemetry
from VENUAI.ai_gateway.gateway import AIGateway, AIServiceUnavailableError, _CircuitBreaker
from VENUAI.ai_gateway.model_registry import ModelCapability, get_catalog, get_model, get_models_with_capability
from VENUAI.ai_gateway.model_router import TaskType, reasoning_effort_for, route
from VENUAI.ai_gateway.provider import build_chat_kwargs, strip_reasoning


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.setattr(telemetry, "DB_PATH", tmp_path / "r.db")
    telemetry.init_db()
    monkeypatch.setattr(gw_mod.time, "sleep", lambda *_: None)


# ── registry ──────────────────────────────────────────────────────────────────

def test_registry_has_current_models_and_no_retired():
    ids = {m.model_id for m in get_catalog()}
    assert ids == {
        "openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b",
        "openai/gpt-oss-safeguard-20b", "meta-llama/llama-prompt-guard-2-86m",
    }
    assert not any("llama-3.3" in i or "llama-3.1" in i for i in ids)


def test_registry_capabilities():
    assert [m.model_id for m in get_models_with_capability(ModelCapability.JUDGE)] == ["qwen/qwen3.8-27b"]
    assert get_models_with_capability(ModelCapability.SAFETY)[0].model_id == "openai/gpt-oss-safeguard-20b"
    assert get_models_with_capability(ModelCapability.PROMPT_GUARD)[0].model_id == "meta-llama/llama-prompt-guard-2-86m"
    assert get_model("openai/gpt-oss-120b").context_window == 131_072
    assert get_model("meta-llama/llama-prompt-guard-2-86m").context_window == 512


# ── router ────────────────────────────────────────────────────────────────────

def test_router_generation_order_and_excludes_classifiers():
    ids = [m.model_id for m in route(TaskType.GENERATION)]
    assert ids == ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"]


def test_router_fast_first_is_20b():
    assert route(TaskType.FAST_CLASSIFICATION)[0].model_id == "openai/gpt-oss-20b"


def test_router_override(monkeypatch):
    monkeypatch.setenv("GROQ_MODEL", "qwen/qwen3.8-27b")
    assert route(TaskType.GENERATION)[0].model_id == "qwen/qwen3.8-27b"
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-safeguard-20b")  # classifier is not a valid chat override
    assert route(TaskType.GENERATION)[0].model_id == "openai/gpt-oss-120b"


def test_reasoning_effort_per_task():
    assert reasoning_effort_for(TaskType.FAST_CLASSIFICATION) == "low"
    for t in (TaskType.GENERATION, TaskType.SYNTHESIS, TaskType.VERIFICATION):
        assert reasoning_effort_for(t) == "medium"


# ── timeout config ────────────────────────────────────────────────────────────

def test_groq_timeout_env(monkeypatch):
    assert provider.get_groq_timeout() == 30.0
    monkeypatch.setenv("GROQ_TIMEOUT_SECONDS", "7")
    assert provider.get_groq_timeout() == 7.0
    monkeypatch.setenv("GROQ_TIMEOUT_SECONDS", "bogus")
    assert provider.get_groq_timeout() == 30.0


def test_clients_have_timeout_and_single_retry(monkeypatch):
    monkeypatch.setenv("GROQ_TIMEOUT_SECONDS", "12")
    c = provider.get_groq_client(api_key="k")
    assert c.timeout == 12.0 and c.max_retries == 1
    a = provider.get_async_groq_client(api_key="k")
    assert a.timeout == 12.0 and a.max_retries == 1
    chat = provider.get_chat_groq("openai/gpt-oss-120b", reasoning_effort="medium")
    assert chat.request_timeout == 12.0 and chat.max_retries == 1


# ── reasoning passthrough ─────────────────────────────────────────────────────

def test_chat_kwargs_effort_model():
    kw = build_chat_kwargs("openai/gpt-oss-120b", 1000, "low")
    assert kw["reasoning_effort"] == "low" and kw["include_reasoning"] is False
    assert kw["max_tokens"] > 1000  # headroom for reasoning tokens


def test_chat_kwargs_qwen_hides_reasoning_no_effort():
    kw = build_chat_kwargs("qwen/qwen3.8-27b", 1000, "medium")
    assert kw["reasoning_format"] == "hidden" and "reasoning_effort" not in kw


def test_chat_kwargs_unknown_model_plain():
    assert build_chat_kwargs("some/other", 500, "high") == {"max_tokens": 500}


def test_strip_reasoning():
    assert strip_reasoning("<think>secret</think>answer") == "answer"


class _Msg:
    def __init__(self, c):
        self.content = c


class _Resp:
    def __init__(self, c):
        self.choices = [type("C", (), {"message": _Msg(c)})()]
        self.usage = None


def _fail():
    raise RuntimeError("connect failed")


class _Client:
    def __init__(self, behaviours):
        self.behaviours = behaviours      # model -> callable() returning response or iterator
        self.seen = []
        outer = self

        class _Comp:
            def create(inner, model, messages, max_tokens, temperature, **kw):
                outer.seen.append((model, kw, max_tokens))
                return outer.behaviours[model]()

        self.chat = type("Ch", (), {"completions": _Comp()})()


def _gw(behaviours):
    client = _Client(behaviours)
    gw = AIGateway(client)
    gw._breaker = _CircuitBreaker()
    return gw, client


def test_gateway_complete_passes_effort_to_supported_models_only():
    gw, client = _gw({"openai/gpt-oss-20b": lambda: _Resp("ok")})
    gw.complete(TaskType.FAST_CLASSIFICATION, [{"role": "user", "content": "x"}])
    model, kw, _ = client.seen[0]
    assert model == "openai/gpt-oss-20b" and kw["reasoning_effort"] == "low"

    gw2, client2 = _gw({
        "openai/gpt-oss-120b": _fail,
        "qwen/qwen3.8-27b": lambda: _Resp("from qwen"),
    })
    r = gw2.complete(TaskType.GENERATION, [{"role": "user", "content": "x"}])
    assert r.model_id == "qwen/qwen3.8-27b"
    assert "reasoning_effort" not in client2.seen[-1][1]


# ── streaming ─────────────────────────────────────────────────────────────────

def _chunks(*texts, fail_after=None):
    def gen():
        for i, t in enumerate(texts):
            if fail_after is not None and i == fail_after:
                raise RuntimeError("mid-stream failure")
            yield type("Ch", (), {"choices": [type("X", (), {"delta": type("D", (), {"content": t})()})()]})()
    return gen()


def test_stream_falls_back_before_first_chunk():
    gw, _ = _gw({
        "openai/gpt-oss-120b": _fail,
        "qwen/qwen3.8-27b": lambda: _chunks("he", "llo"),
    })
    s = gw.stream(TaskType.GENERATION, [{"role": "user", "content": "x"}])
    assert s.model_used == "qwen/qwen3.8-27b"
    assert "".join(s) == "hello"
    assert s.ttft_ms is not None and s.latency_ms is not None
    rows = telemetry.recent(5)
    assert rows[0]["model_used"] == "qwen/qwen3.8-27b" and rows[0]["success"] == 1
    assert rows[0]["ttft_ms"] is not None and rows[0]["fallback_count"] == 1


def test_stream_no_fallback_after_first_chunk():
    gw, client = _gw({
        "openai/gpt-oss-120b": lambda: _chunks("a", "b", fail_after=1),
        "qwen/qwen3.8-27b": lambda: _chunks("SHOULD-NOT-APPEAR"),
    })
    s = gw.stream(TaskType.GENERATION, [{"role": "user", "content": "x"}])
    assert next(s) == "a"
    with pytest.raises(RuntimeError, match="mid-stream"):
        next(s)
    assert {m for m, *_ in client.seen} == {"openai/gpt-oss-120b"}
    assert telemetry.recent(1)[0]["success"] == 0


def test_stream_all_models_fail_raises_controlled_error():
    gw, _ = _gw({m: _fail for m in ("openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b")})
    with pytest.raises(AIServiceUnavailableError):
        gw.stream(TaskType.GENERATION, [{"role": "user", "content": "x"}])


def test_astream_yields_tokens():
    gw, _ = _gw({"openai/gpt-oss-120b": lambda: _chunks("x", "y")})

    async def run():
        s = await gw.astream(TaskType.GENERATION, [{"role": "user", "content": "x"}])
        return s.model_used, [t async for t in s]

    model, toks = asyncio.run(run())
    assert model == "openai/gpt-oss-120b" and toks == ["x", "y"]
