"""
Per-request LLM usage meter (contextvar) shared by every gateway call made while serving
one request, including calls made from worker threads that were started with
contextvars.copy_context (see answer_pipeline.ctx_submit).

No numbers are invented: tokens are what the provider reported; an unreported call adds
nothing and flips `complete` to False so callers can tell the total is a lower bound.
"""
from __future__ import annotations

import contextvars
import threading
from typing import Dict, List, Optional


class BudgetExceeded(RuntimeError):
    """Raised before an LLM call when the per-request token ceiling is already spent."""


class UsageMeter:
    def __init__(self, request_ceiling: Optional[int] = None) -> None:
        self._lock = threading.Lock()
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.calls: List[Dict] = []
        self.models: List[str] = []
        self.complete = True
        self.ceiling = request_ceiling
        self.ttft_ms: Optional[float] = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def record(self, model: str, task: str, prompt: Optional[int], completion: Optional[int],
               latency_ms: Optional[float], ttft_ms: Optional[float] = None) -> None:
        with self._lock:
            if prompt is None and completion is None:
                self.complete = False
            self.prompt_tokens += prompt or 0
            self.completion_tokens += completion or 0
            self.calls.append({"model": model, "task": task, "prompt_tokens": prompt,
                               "completion_tokens": completion, "latency_ms": latency_ms})
            if ttft_ms is not None and task == "generation" and self.ttft_ms is None:
                self.ttft_ms = ttft_ms

    def note_model(self, model: str) -> None:
        with self._lock:
            self.models.append(model)

    def check(self) -> None:
        if self.ceiling and self.total_tokens >= self.ceiling:
            raise BudgetExceeded(f"request token ceiling {self.ceiling} reached")

    def usage_dict(self) -> Dict[str, int]:
        return {"prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens}


_current: contextvars.ContextVar = contextvars.ContextVar("venuai_usage_meter", default=None)


def start_meter(request_ceiling: Optional[int] = None) -> UsageMeter:
    m = UsageMeter(request_ceiling)
    _current.set(m)
    return m


def current_meter() -> Optional[UsageMeter]:
    return _current.get()
