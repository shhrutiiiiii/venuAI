"""
Regression test for a real, previously-hidden bug in
VENUAI/agents/orchestrator.py: `classify_intent` and `merge_results` both
referenced `os.getenv(...)` but the module never imported `os`. Every real
call hit `NameError` inside the `try` block and silently fell into the
`except` handler — meaning intent classification ALWAYS returned
`["general"]` and multi-domain synthesis ALWAYS fell back to naive answer
concatenation. The LLM-based classification/synthesis this class exists for
had never actually run. Fixed by routing both through AIGateway (which has
no such missing-import bug) instead of a raw, ad hoc `os.getenv` + Groq call.

Also covers the real authorization gap fixed alongside it: the
"no valid results" fallback in `route()` called
`self._specialists["general"].query(question, history)` without
`allowed_doc_ids`, meaning that one fallback path bypassed per-chunk
retrieval authorization. Fixed to pass it through like every other call site.
"""

import asyncio

import pytest

from VENUAI.agents.orchestrator import OrchestratorAgent
from VENUAI.agents.base_agent import AgentResult


class _FakeChoice:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})()


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]
        self.usage = None


class _FakeGroqClient:
    """Returns canned responses instead of hitting the network — if the
    missing-`os`-import bug were still present, these calls would never even
    be reached because the NameError fires before this client is touched."""

    def __init__(self, response_text):
        self.response_text = response_text
        self.calls = 0

        outer = self

        class _Completions:
            def create(self, model, messages, max_tokens, temperature, **_kw):
                outer.calls += 1
                return _FakeResponse(outer.response_text)

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def test_classify_intent_actually_calls_the_model(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    monkeypatch.delenv("GROQ_MODEL", raising=False)

    fake_client = _FakeGroqClient("technical,labs")
    orchestrator = OrchestratorAgent(fake_client)

    domains = orchestrator.classify_intent("What is the range of the Agni-V missile?")

    assert fake_client.calls == 1  # proves the model was actually invoked, not short-circuited by NameError
    assert set(domains) == {"technical", "labs"}


def test_classify_intent_falls_back_to_general_only_on_real_failure(monkeypatch):
    """Falling back to ['general'] should only happen when the model call
    genuinely fails — not on every call due to a missing import."""
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")

    class _BrokenClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise RuntimeError("simulated real failure")

    orchestrator = OrchestratorAgent(_BrokenClient())
    domains = orchestrator.classify_intent("anything")
    assert domains == ["general"]


def test_merge_results_actually_calls_the_model(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    monkeypatch.delenv("GROQ_MODEL", raising=False)

    fake_client = _FakeGroqClient("a unified synthesized answer")
    orchestrator = OrchestratorAgent(fake_client)

    results = [
        AgentResult(agent_name="TechnicalAgent", answer="technical details", confidence=0.8),
        AgentResult(agent_name="LabsAgent", answer="lab details", confidence=0.7),
    ]
    merged = orchestrator.merge_results(results, "compare X and Y")

    assert fake_client.calls == 1
    assert merged.answer == "a unified synthesized answer"
    assert merged.confidence == pytest.approx(0.75)


def test_route_fallback_path_passes_allowed_doc_ids(monkeypatch):
    """When every specialist raises (so valid_results is empty), the
    fallback call to the general specialist must still be authorization-
    filtered, not an unfiltered bypass."""
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")

    # "technical,labs" → two domains → the parallel fan-out branch, where
    # the "no valid_results" fallback actually lives.
    orchestrator = OrchestratorAgent(_FakeGroqClient("technical,labs"))

    captured = {}

    def failing_query(*a, **k):
        raise RuntimeError("fail")

    def spy_query(question, history=None, allowed_doc_ids=None):
        captured["allowed_doc_ids"] = allowed_doc_ids
        return AgentResult(agent_name="GeneralAgent", answer="ok", confidence=0.5)

    # Every specialist (including "general") fails during fan-out...
    for specialist in orchestrator._specialists.values():
        specialist.query = failing_query
    # ...except the fallback call afterwards, which we spy on.
    orchestrator._specialists["general"].query = spy_query

    async def run():
        return await orchestrator.route("x", history=None, allowed_doc_ids=["doc-1"])

    asyncio.run(run())
    assert captured.get("allowed_doc_ids") == ["doc-1"]
