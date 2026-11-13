"""LLM-as-judge (RAGAS-style, implemented in-house).

Judge model is qwen/qwen3.8-27b: a different family from the gpt-oss generator,
which limits self-preference bias. Temperature 0, JSON output, on-disk cache.
A failed judge call yields ``None`` + an error string -- never a silent 0.
"""
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("drdo-rag")

JUDGE_MODEL = os.getenv("EVAL_JUDGE_MODEL", "qwen/qwen3.8-27b")
CACHE_DIR = Path(__file__).parent / ".judge_cache"


class JudgeError(Exception):
    pass


def _extract_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            raise JudgeError(f"no JSON in judge output: {text[:120]!r}")
        try:
            return json.loads(m.group(0))
        except Exception as e:
            raise JudgeError(f"bad JSON from judge: {e}")


def _ctx_block(chunks: List[str], limit: int = 1200) -> str:
    return "\n".join(f"[{i + 1}] {c[:limit]}" for i, c in enumerate(chunks))


class Judge:
    def __init__(self, call_fn: Optional[Callable[[str, str], str]] = None,
                 model: str = JUDGE_MODEL, cache_dir: Optional[Path] = CACHE_DIR,
                 retries: int = 3):
        """call_fn(system, user) -> raw text. Default uses the Groq SDK."""
        self.model = model
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.retries = retries
        self._call = call_fn or self._groq_call
        self.calls = 0
        self.cache_hits = 0
        self.failures: List[str] = []

    def _groq_call(self, system: str, user: str) -> str:
        from groq import Groq
        key = os.getenv("GROQ_API_KEY")
        if not key:
            raise JudgeError("GROQ_API_KEY not set; cannot run LLM judge")
        client = getattr(self, "_client", None) or Groq(api_key=key)
        self._client = client
        resp = client.chat.completions.create(
            model=self.model, temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return resp.choices[0].message.content or ""

    def _ask(self, name: str, system: str, user: str) -> dict:
        key = hashlib.sha256(json.dumps([self.model, name, system, user]).encode()).hexdigest()
        path = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if path and path.exists():
            self.cache_hits += 1
            return json.loads(path.read_text(encoding="utf-8"))
        last = None
        for attempt in range(self.retries):
            try:
                self.calls += 1
                out = _extract_json(self._call(system, user))
                if path:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(out), encoding="utf-8")
                return out
            except Exception as e:  # rate limits, parse errors, network
                last = e
                time.sleep(min(2 ** attempt * 2, 20) if "rate" in str(e).lower() or "429" in str(e) else 0)
        raise JudgeError(f"{name}: {last}")

    def _safe(self, name: str, fn: Callable[[], float]) -> Tuple[Optional[float], Optional[str]]:
        try:
            return fn(), None
        except Exception as e:
            msg = f"{name}: {e}"
            self.failures.append(msg)
            return None, msg

    # ── metrics ────────────────────────────────────────────────────────────
    def faithfulness(self, answer: str, chunks: List[str]):
        """Claim-level: decompose answer into atomic claims; fraction supported by context."""
        def run():
            out = self._ask("faith", "You are a strict fact-checking judge. Reply with JSON only.",
                f"Context:\n{_ctx_block(chunks)}\n\nAnswer:\n{answer}\n\n"
                "Split the answer into atomic factual claims (ignore formatting, greetings, citations). "
                "For each claim decide if the context alone supports it. "
                'Return {"claims":[{"claim":str,"supported":true|false}]}.')
            claims = out.get("claims")
            if not isinstance(claims, list):
                raise JudgeError("missing claims list")
            if not claims:
                return None  # nothing to verify (e.g. abstention)
            return sum(1 for c in claims if c.get("supported") is True) / len(claims)
        return self._safe("faithfulness", run)

    def answer_relevancy(self, question: str, answer: str):
        def run():
            out = self._ask("rel", "You are a strict judge. Reply with JSON only.",
                f"Question:\n{question}\n\nAnswer:\n{answer}\n\n"
                "Score 1-5 how directly and completely the answer addresses the question "
                "(1 = off-topic, 5 = fully addresses it, no padding). "
                'Return {"score":int,"reason":str}.')
            s = float(out["score"])
            if not 1 <= s <= 5:
                raise JudgeError(f"score out of range: {s}")
            return (s - 1) / 4
        return self._safe("answer_relevancy", run)

    def context_precision(self, question: str, reference: str, chunks: List[str]):
        """Rank-aware (average precision) over per-chunk usefulness verdicts."""
        def run():
            if not chunks:
                raise JudgeError("no chunks")
            out = self._ask("cprec", "You are a strict judge. Reply with JSON only.",
                f"Question:\n{question}\n\nReference answer:\n{reference}\n\nRetrieved chunks:\n{_ctx_block(chunks)}\n\n"
                "For each chunk decide whether it contains information useful for deriving the reference answer. "
                'Return {"relevant":[true|false,...]} with exactly one entry per chunk, in order.')
            rel = out.get("relevant")
            if not isinstance(rel, list) or len(rel) != len(chunks):
                raise JudgeError("relevant list length mismatch")
            hits, ap = 0, 0.0
            for i, r in enumerate(rel, 1):
                if r is True:
                    hits += 1
                    ap += hits / i
            return ap / hits if hits else 0.0
        return self._safe("context_precision", run)

    def context_recall(self, reference: str, chunks: List[str]):
        """Fraction of reference-answer statements attributable to the context."""
        def run():
            out = self._ask("crec", "You are a strict judge. Reply with JSON only.",
                f"Reference answer:\n{reference}\n\nContext:\n{_ctx_block(chunks)}\n\n"
                "Split the reference answer into atomic statements and decide whether each can be "
                'attributed to the context. Return {"statements":[{"statement":str,"attributed":true|false}]}.')
            st = out.get("statements")
            if not isinstance(st, list) or not st:
                raise JudgeError("missing statements")
            return sum(1 for s in st if s.get("attributed") is True) / len(st)
        return self._safe("context_recall", run)
