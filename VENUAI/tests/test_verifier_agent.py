"""
Regression test for a real, previously-hidden bug in VENUAI/verifier_agent.py:
`check_consistency()` referenced `os.getenv(...)` but the module never
imported `os`. Every real call hit `NameError`, was caught by the method's
own `except Exception`, and fell into "fail open" (`return True`) — meaning
the LLM-based consistency check had never actually run; every answer was
unconditionally treated as consistent with its question. Fixed by routing
through AIGateway (which has no such missing-import bug), the same fix
already applied to the identical bug class in orchestrator.py and
base_agent.py.
"""

from VENUAI.verifier_agent import VerifierAgent


class _FakeChoice:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})()


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]
        self.usage = None


class _FakeGroqClient:
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


def test_check_consistency_actually_calls_the_model_for_yes(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    monkeypatch.delenv("GROQ_MODEL", raising=False)

    fake_client = _FakeGroqClient("YES")
    verifier = VerifierAgent(fake_client)

    result = verifier.check_consistency("The range is 500km.", "What is the range?")

    assert fake_client.calls == 1  # proves the model was actually invoked
    assert result is True


def test_check_consistency_actually_calls_the_model_for_no(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    monkeypatch.delenv("GROQ_MODEL", raising=False)

    fake_client = _FakeGroqClient("NO")
    verifier = VerifierAgent(fake_client)

    result = verifier.check_consistency("The sky is blue.", "What is the range?")

    assert fake_client.calls == 1
    assert result is False


def test_check_consistency_returns_unknown_not_true_on_failure(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")

    class _BrokenClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise RuntimeError("simulated real failure")

    verifier = VerifierAgent(_BrokenClient())
    result = verifier.check_consistency("anything", "anything")
    assert result is None  # fail-closed: unknown is never reported as 'consistent'
