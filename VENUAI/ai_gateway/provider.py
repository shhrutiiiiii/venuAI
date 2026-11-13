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

import os
import re
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator, List, Optional

# ── Central Groq client construction ──────────────────────────────────────────
# Every Groq SDK / ChatGroq client in VENUAI/ must be built here so timeout and
# retry policy live in exactly one place. SDK max_retries is 1: the AIGateway
# owns retry/backoff/fallback; stacking SDK retries on top multiplies latency.

DEFAULT_GROQ_TIMEOUT_SECONDS = 30.0
GROQ_SDK_MAX_RETRIES = 1

_client_lock = threading.Lock()
_sync_client = None


def get_groq_timeout() -> float:
    try:
        v = float(os.getenv("GROQ_TIMEOUT_SECONDS", DEFAULT_GROQ_TIMEOUT_SECONDS))
        return v if v > 0 else DEFAULT_GROQ_TIMEOUT_SECONDS
    except ValueError:
        return DEFAULT_GROQ_TIMEOUT_SECONDS


def get_groq_client(api_key: Optional[str] = None):
    """Shared sync Groq client with timeout + max_retries=1. Cached when the
    key comes from the environment."""
    global _sync_client
    from groq import Groq
    if api_key is not None:
        return Groq(api_key=api_key, timeout=get_groq_timeout(), max_retries=GROQ_SDK_MAX_RETRIES)
    with _client_lock:
        if _sync_client is None:
            _sync_client = Groq(
                api_key=os.getenv("GROQ_API_KEY"),
                timeout=get_groq_timeout(), max_retries=GROQ_SDK_MAX_RETRIES,
            )
        return _sync_client


def get_async_groq_client(api_key: Optional[str] = None):
    from groq import AsyncGroq
    return AsyncGroq(
        api_key=api_key or os.getenv("GROQ_API_KEY"),
        timeout=get_groq_timeout(), max_retries=GROQ_SDK_MAX_RETRIES,
    )


def get_chat_groq(model_name: str, temperature: float = 0.2, max_tokens: int = 1500,
                  reasoning_effort: Optional[str] = None, **kwargs):
    """LangChain ChatGroq with the same timeout/retry policy. Used only where
    LangGraph requires a LangChain chat model (the ReAct agent)."""
    from langchain_groq import ChatGroq
    kw = dict(
        api_key=os.getenv("GROQ_API_KEY"), model_name=model_name,
        temperature=temperature, max_tokens=max_tokens,
        timeout=get_groq_timeout(), max_retries=GROQ_SDK_MAX_RETRIES,
    )
    if reasoning_effort:
        kw["reasoning_effort"] = reasoning_effort
    kw.update(kwargs)
    return ChatGroq(**kw)


# Reasoning tokens are billed against max_tokens on Groq, so a reasoning model
# given max_tokens=1500 can spend it all thinking and return an empty answer.
_REASONING_HEADROOM = {"low": 512, "medium": 1536, "high": 3072}
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def strip_reasoning(text: Optional[str]) -> str:
    """Defensive: remove any inline <think>...</think> block from content."""
    if not text:
        return text or ""
    return _THINK_RE.sub("", text).strip()


def build_chat_kwargs(model_id: str, max_tokens: int, reasoning_effort: Optional[str]) -> dict:
    """Per-model request params: reasoning controls only for models that
    support them, never leaking reasoning text into message content, and
    max_tokens widened to leave room for reasoning tokens."""
    from VENUAI.ai_gateway.model_registry import get_model
    entry = get_model(model_id)
    style = entry.reasoning_style if entry else ""
    kwargs: dict = {"max_tokens": max_tokens}
    if style == "effort":
        effort = reasoning_effort or "medium"
        kwargs["reasoning_effort"] = effort
        kwargs["include_reasoning"] = False
        kwargs["max_tokens"] = max_tokens + _REASONING_HEADROOM.get(effort, 1536)
    elif style == "hidden":
        kwargs["reasoning_format"] = "hidden"
        kwargs["max_tokens"] = max_tokens + _REASONING_HEADROOM["medium"]
    return kwargs


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
        reasoning_effort: Optional[str] = None,
    ) -> CompletionResult:
        ...

    def stream(
        self,
        model_id: str,
        messages: List[dict],
        max_tokens: int,
        temperature: float,
        reasoning_effort: Optional[str] = None,
    ) -> Iterator[str]:
        raise NotImplementedError(f"{self.name} provider does not support streaming.")


class GroqProvider(ModelProvider):
    name = "groq"

    def __init__(self, groq_client) -> None:
        """
        Args:
            groq_client: a `groq.Groq` instance, normally from get_groq_client()
                (timeout + max_retries=1 applied there).
        """
        self._client = groq_client

    def complete(
        self,
        model_id: str,
        messages: List[dict],
        max_tokens: int,
        temperature: float,
        reasoning_effort: Optional[str] = None,
    ) -> CompletionResult:
        resp = self._client.chat.completions.create(
            model=model_id,
            messages=messages,
            temperature=temperature,
            **build_chat_kwargs(model_id, max_tokens, reasoning_effort),
        )
        usage = getattr(resp, "usage", None)
        return CompletionResult(
            content=strip_reasoning(resp.choices[0].message.content),
            model_id=model_id,
            provider=self.name,
            input_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
            output_tokens=getattr(usage, "completion_tokens", None) if usage else None,
        )

    def stream(
        self,
        model_id: str,
        messages: List[dict],
        max_tokens: int,
        temperature: float,
        reasoning_effort: Optional[str] = None,
    ) -> Iterator[str]:
        """Yields content deltas only (reasoning is excluded by request params)."""
        resp = self._client.chat.completions.create(
            model=model_id,
            messages=messages,
            temperature=temperature,
            stream=True,
            **build_chat_kwargs(model_id, max_tokens, reasoning_effort),
        )
        in_think = False
        for chunk in resp:
            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta.content or ""
            # Defensive <think> filtering for models that ignore reasoning_format.
            if "<think>" in delta:
                in_think = True
                delta = delta.split("<think>")[0]
            if in_think:
                if "</think>" in delta:
                    in_think = False
                    delta = delta.split("</think>", 1)[1]
                else:
                    delta = ""
            if delta:
                yield delta


class OpenAIProvider(ModelProvider):
    """Not implemented — no OPENAI_API_KEY is configured anywhere in this
    deployment. Declared only to satisfy the provider-abstraction interface
    (spec section 10) without fabricating a working OpenAI integration."""
    name = "openai"

    def complete(self, model_id, messages, max_tokens, temperature, reasoning_effort=None) -> CompletionResult:
        raise NotImplementedError(
            "OpenAIProvider is not implemented — no OPENAI_API_KEY is configured. "
            "This is a declared interface for a future phase, not a working integration."
        )


class LocalProvider(ModelProvider):
    """Not implemented — no local model runtime (e.g. Ollama, vLLM) is
    configured anywhere in this deployment. See OpenAIProvider docstring."""
    name = "local"

    def complete(self, model_id, messages, max_tokens, temperature, reasoning_effort=None) -> CompletionResult:
        raise NotImplementedError(
            "LocalProvider is not implemented — no local model runtime is configured. "
            "This is a declared interface for a future phase, not a working integration."
        )
