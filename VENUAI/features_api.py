"""Feature register: GET /features and POST /documents/{id}/summaries.

GET /features reports, for every optional / token-spending mechanism, whether it
is currently enabled (derived from the SAME env flags the modules read - never
hard-coded) plus an honest token-cost note. Nothing here turns a feature on or
spends LLM tokens; see Documents/AI/token-costs.md for the full register.

POST /documents/{id}/summaries builds RAPTOR summaries on demand (explicit user
action only, never automatic). 503 when RAPTOR is off.
"""
from __future__ import annotations

import os
from typing import Callable, List

from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from VENUAI import authz
from VENUAI.auth import AuthenticatedUser, get_current_user

router = APIRouter(tags=["features"])


def _on(name: str, default: str = "off") -> bool:
    return os.getenv(name, default).strip().lower() in ("on", "1", "true", "yes")


def _not_off(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "off")


def _spec(key, label, enabled: Callable[[], bool], cost, note, enable_with, requires="", per_user=False):
    return {"key": key, "label": label, "_enabled": enabled, "per_user_opt_in": per_user,
            "token_cost": cost, "token_cost_note": note, "enable_with": enable_with, "requires": requires}


def _specs() -> List[dict]:
    from VENUAI import memory_manager, otel_genai, raptor, safety_classifiers, semantic_cache
    return [
        _spec("long_term_memory", "Long-term memory", memory_manager.long_term_memory_enabled, "low",
              "After each turn, one fast-model call extracts memories (skipped for trivial turns); recall is local embedding search (no LLM).",
              "LONG_TERM_MEMORY=on", "Each user must also opt in", per_user=True),
        _spec("voice", "Voice (speech in / out)", lambda: _on("VOICE_ENABLED"), "low",
              "Whisper transcription per recording and Orpheus TTS per Listen click; only on explicit user action, never automatic.",
              "VOICE_ENABLED=on", "Accept Orpheus terms in the Groq console; pip install requirements-voice.txt"),
        _spec("raptor", "RAPTOR document summaries", raptor.enabled,
              "medium", "Up to RAPTOR_MAX_CALLS (default 6) LLM summarisation calls per document build, plus retrieval of summaries at query time.",
              "RAPTOR=on", "At least 4 chunks in the document"),
        _spec("semantic_cache", "Answer cache", lambda: semantic_cache.mode() != "off", "none",
              "Saves tokens: repeat identical questions are served without an LLM call.", "SEMANTIC_CACHE=exact|semantic|off"),
        _spec("a2a", "Agent-to-agent API", lambda: _on("A2A_ENABLED"), "none",
              "Exposes an endpoint; costs tokens only when an external agent calls it.", "A2A_ENABLED=on"),
        _spec("otel", "OpenTelemetry export", otel_genai.enabled,
              "none", "Telemetry only; no LLM calls.", "OTEL_EXPORTER_OTLP_ENDPOINT=<collector url>", "pip install requirements-otel.txt"),
        _spec("contextual_chunking", "Contextual chunking", lambda: _not_off("CONTEXTUAL_CHUNKING"), "high",
              "One fast-model call per chunk at upload time (capped at 200 per document).", "CONTEXTUAL_CHUNKING=true|false"),
        _spec("query_rewrite", "Query rewriting", lambda: _not_off("QUERY_REWRITE_ENABLED"), "low",
              "About 1 fast-model call per question to rewrite / expand the query.", "QUERY_REWRITE_ENABLED=true|false"),
        _spec("corrective_rag", "Corrective RAG", lambda: _not_off("CORRECTIVE_RAG", "on"), "medium",
              "When retrieval looks weak: up to CORRECTIVE_MAX_ITERS (2) rounds, each with grader + re-retrieval LLM calls.", "CORRECTIVE_RAG=on|off"),
        _spec("safeguard", "Safeguard policy classifier", lambda: safety_classifiers.safeguard_enabled(), "medium",
              "One gpt-oss-safeguard-20b call per request.", "SAFEGUARD_ENABLED=true|false"),
        _spec("prompt_guard", "Prompt-injection guard", lambda: safety_classifiers.prompt_guard_enabled(), "low",
              "One small Llama Prompt Guard call per request (86M classifier, very few tokens).", "PROMPT_GUARD_ENABLED=true|false"),
        _spec("followups", "Follow-up suggestions", lambda: _not_off("FOLLOWUPS", "on"), "low",
              "One fast-model call per answered question.", "FOLLOWUPS=on|off"),
        _spec("local_faithfulness", "Local faithfulness check (HHEM)", lambda: _on("LOCAL_FAITHFULNESS"), "none",
              "Runs a local model on CPU; replaces/avoids LLM claim checks, zero Groq tokens.", "LOCAL_FAITHFULNESS=on", "Local HHEM model download"),
    ]


def feature_list() -> List[dict]:
    out = []
    for s in _specs():
        item = {k: v for k, v in s.items() if k != "_enabled"}
        try:
            item["enabled"] = bool(s["_enabled"]())
        except Exception:  # noqa: BLE001 - a broken probe must not break the page
            item["enabled"] = False
        out.append(item)
    return out


@router.get("/features")
def get_features(user: AuthenticatedUser = Depends(get_current_user)) -> dict:
    return {"features": feature_list()}


@router.post("/documents/{doc_id}/summaries")
async def build_document_summaries(doc_id: str, user: AuthenticatedUser = Depends(get_current_user)) -> dict:
    from VENUAI import rag_server, raptor
    meta = rag_server.load_metadata()
    authz.check_doc_access(meta, doc_id, user, action="delete")  # 404 / 403 before anything else
    if not raptor.enabled():
        raise HTTPException(status_code=503, detail="RAPTOR disabled")
    got = rag_server.get_vector_store().get(where={"doc_id": doc_id})
    chunks = [{"text": t} for t in (got or {}).get("documents", []) or []]
    stored = await run_in_threadpool(raptor.build_summaries, doc_id, user.tenant_id, chunks)
    return {"doc_id": doc_id, "summaries_stored": stored, "max_llm_calls": raptor.max_calls()}
