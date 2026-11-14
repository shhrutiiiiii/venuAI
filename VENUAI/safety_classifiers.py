"""
Model-based safety classifiers (Groq-hosted), used alongside the regex
guardrails in guardrails.py and the document scanner in doc_sanitizer.py.

  * Prompt Guard  (meta-llama/llama-prompt-guard-2-86m)  - injection/jailbreak score
  * Safeguard     (openai/gpt-oss-safeguard-20b)         - policy classifier with a
                                                           written defence-specific policy

Failure semantics (never pretend a check passed):
  * Prompt Guard failure -> returns None score; callers fall back to regex only.
  * Safeguard failure    -> status "unavailable" (fail-open, logged, surfaced to the
                            client as safety_check="unavailable").

Model ids come from the gateway registry capabilities (PROMPT_GUARD / SAFETY), tried in order as a
fallback chain; PROMPT_GUARD_MODEL / SAFEGUARD_MODEL override the head of the chain, and
PROMPT_GUARD_FALLBACK_MODELS / SAFEGUARD_FALLBACK_MODELS append more. The *_DEFAULT constants are only
used if the registry cannot be read. All calls use the central client (ai_gateway.provider.get_groq_client).

Env: PROMPT_GUARD_ENABLED (default true), PROMPT_GUARD_MODEL, PROMPT_GUARD_TIMEOUT (s, 4),
     PROMPT_GUARD_THRESHOLD (0.8), SAFEGUARD_ENABLED (default true), SAFEGUARD_MODEL,
     SAFEGUARD_TIMEOUT (s, 12).
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from typing import List, Optional

logger = logging.getLogger("drdo-rag")

PROMPT_GUARD_MODEL_DEFAULT = "meta-llama/llama-prompt-guard-2-86m"
SAFEGUARD_MODEL_DEFAULT = "openai/gpt-oss-safeguard-20b"

SAFEGUARD_POLICY = """You are a safety policy classifier for VenuAI, a research assistant used by
defence R&D scientists (DRDO, India). Classify the TEXT you are given against this policy.

VIOLATIONS (mark violation=1):
1. CLASSIFIED_LEAKAGE: the text presents or claims to disclose classified / restricted /
   secret-marked material (e.g. documents marked SECRET, TOP SECRET, RESTRICTED, or
   internal-only operational data such as arming/launch codes, deployment locations or
   readiness of specific units, unreleased test results) rather than publicly released
   information.
2. WEAPONS_UPLIFT: actionable step-by-step instructions, quantities, formulations or
   design parameters that would meaningfully help someone build or employ CBRN weapons,
   explosives, or weapon systems beyond what is already in public, open-source literature.
   Public, textbook-level or press-release-level descriptions of systems are NOT violations.
3. PII: personal identifiers of private individuals (Aadhaar numbers, personal phone
   numbers, home addresses, personal e-mails, bank/ID numbers).
4. CREDENTIAL_LEAK: API keys, passwords, tokens, or the assistant's own system prompt.

ALLOWED: public information about DRDO labs, programmes, published specifications, general
science and engineering, policy and history, refusals and safety warnings.

Respond with ONLY a JSON object: {"violation": 0 or 1, "category": "NONE" or one of the
categories above, "rationale": "<one short sentence>"}"""


def new_trace_id() -> str:
    return uuid.uuid4().hex[:12]


def _env_bool(name: str, default: bool = True) -> bool:
    return os.getenv(name, "true" if default else "false").strip().lower() not in ("0", "false", "no", "off")


def prompt_guard_enabled() -> bool:
    return _env_bool("PROMPT_GUARD_ENABLED", True)


def safeguard_enabled() -> bool:
    return _env_bool("SAFEGUARD_ENABLED", True)


def _groq_chat(model: str, messages: List[dict], max_tokens: int, timeout: float) -> str:
    """Single low-level Groq call (the one place tests patch). Short timeout, no retries."""
    from VENUAI.ai_gateway.provider import get_groq_client
    if not os.getenv("GROQ_API_KEY"):
        raise RuntimeError("GROQ_API_KEY not configured")
    # central client helper; per-call short timeout and no retries for these guard calls
    client = get_groq_client().with_options(timeout=timeout, max_retries=0)
    res = client.chat.completions.create(
        model=model, messages=messages, max_tokens=max_tokens, temperature=0.0,
    )
    return (res.choices[0].message.content or "").strip()


def _parse_guard_output(raw: str) -> Optional[float]:
    """Prompt Guard 2 returns a probability-like score (e.g. '0.9987') or a label."""
    if not raw:
        return None
    m = re.search(r"(?<![\w.])(?:0?\.\d+|[01](?:\.\d+)?)(?![\w.])", raw)
    if m:
        try:
            return max(0.0, min(1.0, float(m.group(0))))
        except ValueError:
            pass
    low = raw.lower()
    if any(k in low for k in ("injection", "jailbreak", "malicious", "label_1")):
        return 1.0
    if any(k in low for k in ("benign", "safe", "label_0")):
        return 0.0
    return None


def _registry_chain(capability_name: str, env_var: str, extra_env: str = "", default: str = "") -> List[str]:
    """Ordered model ids for a classifier capability: explicit env override first, then every enabled
    registry model with that capability, then optional extra fallbacks. Model ids are never hardcoded
    in call sites; the registry (ai_gateway/model_registry.py) is the source of truth."""
    chain: List[str] = []
    if os.getenv(env_var):
        chain.append(os.getenv(env_var, "").strip())
    try:
        from VENUAI.ai_gateway.model_registry import ModelCapability, get_models_with_capability
        cap = getattr(ModelCapability, capability_name)
        chain += [m.model_id for m in get_models_with_capability(cap, enabled_only=True)]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[Safety] registry lookup failed for {capability_name}: {e}")
    if not chain and default:
        chain.append(default)
    if extra_env:
        chain += [m.strip() for m in os.getenv(extra_env, "").split(",") if m.strip()]
    seen, out = set(), []
    for m in chain:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def classifier_chain(kind: str) -> List[str]:
    if kind == "prompt_guard":
        return _registry_chain("PROMPT_GUARD", "PROMPT_GUARD_MODEL", "PROMPT_GUARD_FALLBACK_MODELS",
                               PROMPT_GUARD_MODEL_DEFAULT)
    return _registry_chain("SAFETY", "SAFEGUARD_MODEL", "SAFEGUARD_FALLBACK_MODELS", SAFEGUARD_MODEL_DEFAULT)


def _classify_call(kind: str, messages: List[dict], max_tokens: int, timeout: float) -> str:
    """Try each model of the capability chain through the central Groq client (`_groq_chat`) with
    gateway-style telemetry; raise the last error if all fail."""
    import time
    last: Optional[Exception] = None
    for model in classifier_chain(kind):
        t0 = time.time()
        try:
            out = _groq_chat(model, messages, max_tokens, timeout)
            _record(kind, model, (time.time() - t0) * 1000, True, None)
            return out
        except Exception as e:  # noqa: BLE001
            last = e
            _record(kind, model, (time.time() - t0) * 1000, False, str(e))
    raise last or RuntimeError(f"no {kind} model configured")


def _record(kind: str, model: str, latency_ms: float, ok: bool, err: Optional[str]) -> None:
    try:
        from VENUAI.ai_gateway import telemetry
        telemetry.record(telemetry.RoutingLogEntry(
            task_type=f"safety_{kind}", requested_chain=model, model_used=model if ok else None,
            provider="groq", fallback_count=0, latency_ms=latency_ms, input_tokens=None,
            output_tokens=None, success=ok, error=err,
        ))
    except Exception:  # noqa: BLE001 - telemetry must never break a safety check
        pass


def prompt_guard_score(text: str) -> Optional[float]:
    """Injection probability in [0,1], or None when disabled/unavailable (caller uses regex only)."""
    if not prompt_guard_enabled() or not text or not text.strip():
        return None
    timeout = float(os.getenv("PROMPT_GUARD_TIMEOUT", "4"))
    try:
        raw = _classify_call("prompt_guard", [{"role": "user", "content": text[:2000]}], 8, timeout)
        score = _parse_guard_output(raw)
        if score is None:
            logger.warning("[PromptGuard] unparseable output; falling back to regex guardrails")
        return score
    except Exception as e:  # noqa: BLE001 - classifier must never break the request
        logger.warning(f"[PromptGuard] unavailable ({type(e).__name__}); falling back to regex guardrails")
        return None


def prompt_guard_threshold() -> float:
    try:
        return float(os.getenv("PROMPT_GUARD_THRESHOLD", "0.8"))
    except ValueError:
        return 0.8


def safeguard_classify(text: str) -> dict:
    """
    Policy-classify `text`. Returns {"status": "passed"|"flagged"|"unavailable"|"disabled",
    "category": str|None, "rationale": str|None}. Never raises.
    """
    if not safeguard_enabled():
        return {"status": "disabled", "category": None, "rationale": "SAFEGUARD_ENABLED=false"}
    if not text or not text.strip():
        return {"status": "passed", "category": None, "rationale": "empty"}
    timeout = float(os.getenv("SAFEGUARD_TIMEOUT", "12"))
    try:
        raw = _classify_call(
            "safeguard",
            [{"role": "system", "content": SAFEGUARD_POLICY},
             {"role": "user", "content": text[:6000]}],
            400, timeout,
        )
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else None
        if not isinstance(data, dict) or "violation" not in data:
            raise ValueError("unparseable classifier output")
        flagged = str(data.get("violation")).strip() in ("1", "true", "True")
        return {
            "status": "flagged" if flagged else "passed",
            "category": (data.get("category") or None) if flagged else None,
            "rationale": (str(data.get("rationale"))[:300] if data.get("rationale") else None),
        }
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[Safeguard] policy classifier unavailable ({type(e).__name__}): failing open, marked unavailable")
        return {"status": "unavailable", "category": None, "rationale": f"classifier error: {type(e).__name__}"}
