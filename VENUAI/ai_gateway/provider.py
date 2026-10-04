"""
Model provider abstraction.

GroqProvider wraps the *existing* `groq` SDK client already used throughout
this codebase (VENUAI/rag_server.py, VENUAI/agents/*) — it is not a new
dependency or a parallel client, just a thin, uniform call surface so
AIGateway doesn't need to know provider-specific SDK shapes.

OpenAIProvider / LocalProvider are declared as the interface section 10 of
the spec asks for, so a real implementation can be dropped in later without
redesigning AIGateway — but neither has a working body, because no OpenAI
API key or local model runtime is configured in this environment. Calling
either raises NotImplementedError with a clear message. This is the
"mark it clearly as blocked" instruction from the spec, not a fabricated
integration.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class CompletionResult:
    content: str
    model_id: str
    provider: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


class ModelProvider(ABC):
    name: str = "unknown"

    @abstractmethod
    def complete(
        self,
        model_id: str,
        messages: List[dict],
        max_tokens: int,
        temperature: float,
    ) -> CompletionResult:
        ...


class GroqProvider(ModelProvider):
    name = "groq"

    def __init__(self, groq_client) -> None:
        """
        Args:
            groq_client: an already-constructed `groq.Groq(api_key=...)`
                instance — callers pass in the same client this codebase
                already builds elsewhere, so there is exactly one place
                (wherever the caller constructs `Groq(api_key=GROQ_API_KEY)`)
                that owns the API key.
        """
        self._client = groq_client

    def complete(
        self,
        model_id: str,
        messages: List[dict],
        max_tokens: int,
        temperature: float,
    ) -> CompletionResult:
        resp = self._client.chat.completions.create(
            model=model_id,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        usage = getattr(resp, "usage", None)
        return CompletionResult(
            content=resp.choices[0].message.content,
            model_id=model_id,
            provider=self.name,
            input_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
            output_tokens=getattr(usage, "completion_tokens", None) if usage else None,
        )


class OpenAIProvider(ModelProvider):
    """Not implemented — no OPENAI_API_KEY is configured anywhere in this
    deployment. Declared only to satisfy the provider-abstraction interface
    (spec section 10) without fabricating a working OpenAI integration."""
    name = "openai"

    def complete(self, model_id, messages, max_tokens, temperature) -> CompletionResult:
        raise NotImplementedError(
            "OpenAIProvider is not implemented — no OPENAI_API_KEY is configured. "
            "This is a declared interface for a future phase, not a working integration."
        )


class LocalProvider(ModelProvider):
    """Not implemented — no local model runtime (e.g. Ollama, vLLM) is
    configured anywhere in this deployment. See OpenAIProvider docstring."""
    name = "local"

    def complete(self, model_id, messages, max_tokens, temperature) -> CompletionResult:
        raise NotImplementedError(
            "LocalProvider is not implemented — no local model runtime is configured. "
            "This is a declared interface for a future phase, not a working integration."
        )
