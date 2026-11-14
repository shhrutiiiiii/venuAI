"""
DRDO VenuAI — Agent Tools
===========================
LangChain @tool-decorated functions for the ReAct agentic loop.
Each tool wraps an existing VenuAI capability and exposes it to the LLM.

Two ways to get tools:
  ALL_TOOLS            — module-level, unfiltered tools (kept for backward
                          compatibility with VENUAI/mcp_server.py, which has
                          no per-request user identity to filter by; see
                          Documents/Backend/authentication.md for why MCP
                          tool security is tracked as a separate, larger,
                          still-open item).
  build_agent_tools()  — a per-request factory that binds the calling
                          user's identity and document-visibility set, so
                          the ReAct agent (agent_mode=true in /query) cannot
                          retrieve or list documents the caller isn't
                          authorized to see, and cannot recall another
                          user's memory. Use this one for anything serving
                          real end users. See Documents/Backend/authentication.md.

Available tools (each factory-built tool closes over `allowed_doc_ids` and
`user_id` so the LLM cannot override whose data it sees):
  drdo_kb_search      — hybrid vector + BM25 search of the knowledge base
  drdo_calculator     — safe mathematical expression evaluator
  memory_recall       — retrieve relevant facts from the user's own past sessions
  document_summary    — structured summary of a specific uploaded document
  web_search_drdo     — DuckDuckGo search for public DRDO news
  list_knowledge_base — list documents in the knowledge base the caller can see
  code_execution      — run Python in a real, isolated Docker container (Phase 5,
                        admin-only — see VENUAI/code_sandbox.py)
"""

import concurrent.futures
import contextvars
import logging
import math
import os
import re
import threading
import types
from typing import List, Optional

from langchain_core.tools import tool

logger = logging.getLogger("drdo-rag")

# ── Safe math namespace for drdo_calculator ────────────────────────────────────
_SAFE_MATH_NS = {
    k: getattr(math, k) for k in dir(math) if not k.startswith("_")
}
_SAFE_MATH_NS.update({"abs": abs, "round": round, "min": min, "max": max, "pow": pow})


def _calculate(expression: str) -> str:
    safe_expr = re.sub(r"[^0-9\+\-\*/\.\(\)\s\w]", "", expression)
    try:
        result = eval(safe_expr, {"__builtins__": {}}, _SAFE_MATH_NS)  # noqa: S307
        return f"Result: {result}"
    except ZeroDivisionError:
        return "Error: Division by zero."
    except Exception as e:
        return f"Calculation error: {str(e)}. Expression: '{expression}'"


def _search_web_drdo(query: str) -> str:
    try:
        from duckduckgo_search import DDGS

        # The query is LLM-controlled (ultimately user-influenced via the
        # ReAct agent's tool-call arguments). Without this, a crafted query
        # containing its own "site:" operator — e.g. "ignore that OR
        # site:evil.com" — would be concatenated verbatim into the search
        # string below and could broaden the search engine's effective scope
        # beyond the intended drdo.gov.in/pib.gov.in restriction. Strip any
        # caller-supplied site: tokens so the only one in the final query is
        # the one this function appends itself.
        sanitized_query = re.sub(r"\bsite\s*:\s*\S+", "", query, flags=re.IGNORECASE).strip()

        with DDGS() as ddg:
            results = list(ddg.text(f"DRDO {sanitized_query} site:drdo.gov.in OR site:pib.gov.in", max_results=3))

        if not results:
            return "No web results found."

        parts = []
        for i, r in enumerate(results, 1):
            parts.append(
                f"**{i}. {r.get('title', 'No title')}**\n"
                f"{r.get('body', '')[:250]}\n"
                f"🔗 {r.get('href', '')}"
            )
        return "\n\n".join(parts)
    except ImportError:
        return (
            "Web search is unavailable (duckduckgo-search not installed). "
            "Run: pip install duckduckgo-search"
        )
    except Exception as e:
        logger.error(f"[web_search_drdo] Error: {e}")
        return f"Web search failed: {str(e)}"


def _run_code(code: str) -> str:
    try:
        from VENUAI.code_sandbox import SandboxUnavailableError, run_python

        result = run_python(code)
        if result.timed_out:
            return f"⏱️ {result.stderr}"
        parts = []
        if result.stdout:
            parts.append(f"**stdout:**\n```\n{result.stdout}\n```")
        if result.stderr:
            parts.append(f"**stderr:**\n```\n{result.stderr}\n```")
        parts.append(f"Exit code: {result.exit_code}")
        return "\n\n".join(parts)
    except SandboxUnavailableError as e:
        return f"Code execution sandbox unavailable: {e}"
    except Exception as e:
        logger.error(f"[code_execution] Error: {e}")
        return f"Code execution failed: {str(e)}"


def _search_kb(
    query: str, top_k: int, allowed_doc_ids: Optional[List[str]],
    collected_docs: Optional[list] = None,
) -> str:
    """
    `collected_docs`, when passed, gets every real `Document` this call
    retrieved appended to it — the ReAct route handler
    (`VENUAI.rag_server`'s `agent_mode` branch) uses this to run the same
    claim-level `VerificationPipeline` standard RAG and multi-agent mode
    do, since the LangGraph ReAct loop only ever sees this function's
    formatted string return value, never the underlying `Document`
    objects. `ALL_TOOLS`/MCP callers that don't pass it are unaffected —
    this is additive, not a changed return contract.
    """
    try:
        from VENUAI.rag_server import get_vector_store
        from VENUAI.context_engineer import rank_chunks

        # Shared pipeline (VENUAI.retrieval): dense + cached BM25 -> RRF ->
        # cross-encoder rerank -> relevance floor, multimodal regions included.
        # The agent already writes its own query, so no history rewrite here.
        from VENUAI.retrieval import retrieve_documents
        docs = retrieve_documents(
            get_vector_store(), query, top_k, allowed_doc_ids, rewrite=False,
        ).documents

        ranked = rank_chunks(docs, query)

        if not ranked:
            return "No relevant documents found in the DRDO knowledge base."

        if collected_docs is not None:
            collected_docs.extend(ranked[:top_k])

        parts = []
        for i, doc in enumerate(ranked[:top_k], 1):
            meta = doc.metadata
            src = f"[{meta.get('filename', 'KB')}, p.{meta.get('page', 0) + 1}]"
            parts.append(f"**Result {i}** {src}\n{doc.page_content[:400]}")

        return "\n\n---\n\n".join(parts)
    except Exception as e:
        logger.error(f"[drdo_kb_search] Error: {e}")
        return f"Knowledge base search failed: {str(e)}"


def _recall_memory(query: str, user_id: str) -> str:
    try:
        from VENUAI.memory_manager import get_memory_manager
        mm = get_memory_manager()
        ctx = mm.get_context(session_id="recall-temp", user_id=user_id, query=query)
        parts = []
        if ctx.get("episodic"):
            parts.append(f"**Past sessions:** {ctx['episodic']}")
        if ctx.get("semantic"):
            parts.append("**Relevant memories:**\n" + "\n".join(f"- {m}" for m in ctx["semantic"]))
        return "\n\n".join(parts) if parts else "No relevant past memories found."
    except Exception as e:
        logger.error(f"[memory_recall] Error: {e}")
        return f"Memory recall failed: {str(e)}"


def _summarize_document(doc_id: str, allowed_doc_ids: Optional[List[str]]) -> str:
    try:
        from VENUAI.rag_server import load_metadata
        meta = load_metadata()
        if not meta:
            return "No documents found in the knowledge base."

        if doc_id not in meta:
            return f"Document '{doc_id}' not found. Use list_knowledge_base to see available IDs."

        if allowed_doc_ids is not None and doc_id not in allowed_doc_ids:
            return "You are not authorized to view this document."

        doc = meta[doc_id]
        tags = doc.get("tags", {})
        return (
            f"**Document:** {doc['filename']}\n"
            f"**Pages:** {doc['pages']} | **Chunks:** {doc['chunks']} | "
            f"**Size:** {doc['size_kb']} KB | **Uploaded:** {doc['upload_time'][:10]}\n"
            f"**Domain tags:** {', '.join(tags.get('domain_tags', [])) or 'N/A'}\n"
            f"**DRDO Labs mentioned:** {', '.join(tags.get('drdo_labs', [])) or 'N/A'}\n"
            f"**Key entities:** {', '.join(tags.get('key_entities', [])) or 'N/A'}\n"
            f"**Auto-summary:** {tags.get('summary', 'Not available.')}"
        )
    except Exception as e:
        logger.error(f"[document_summary] Error: {e}")
        return f"Document summary failed: {str(e)}"


def _list_kb(allowed_doc_ids: Optional[List[str]]) -> str:
    try:
        from VENUAI.rag_server import load_metadata
        meta = load_metadata()
        if not meta:
            return "Knowledge base is empty. No documents have been uploaded yet."

        if allowed_doc_ids is not None:
            allowed_set = set(allowed_doc_ids)
            meta = {doc_id: doc for doc_id, doc in meta.items() if doc_id in allowed_set}

        if not meta:
            return "No documents visible to you in the knowledge base."

        lines = ["| # | Filename | Pages | Uploaded | Domains |", "|---|----------|-------|----------|---------|"]
        for i, (doc_id, doc) in enumerate(meta.items(), 1):
            tags = doc.get("tags", {})
            domains = ", ".join(tags.get("domain_tags", []))[:30] or "N/A"
            lines.append(
                f"| {i} | `{doc['filename']}` | {doc['pages']} | "
                f"{doc['upload_time'][:10]} | {domains} |"
            )
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[list_knowledge_base] Error: {e}")
        return f"Failed to list knowledge base: {str(e)}"


# ── Module-level, unfiltered tools ─────────────────────────────────────────────
# Kept for VENUAI/mcp_server.py backward compatibility — MCP has no per-request
# user identity to bind yet (see Documents/Backend/authentication.md). Do not
# use these directly for anything serving real end users; use build_agent_tools().

@tool
def drdo_kb_search(query: str, top_k: int = 5) -> str:
    """
    Search the DRDO knowledge base using hybrid vector + BM25 retrieval.
    Use this tool for questions about DRDO labs, weapons, projects,
    technical specifications, or research. Returns formatted chunks
    with source citations.

    Args:
        query : The search query string.
        top_k : Number of results to return (default 5).
    """
    return _search_kb(query, top_k, allowed_doc_ids=None)


@tool
def drdo_calculator(expression: str) -> str:
    """
    Evaluate mathematical and engineering expressions safely.
    Use for range calculations, velocity / energy formulas, and unit conversions.

    Examples:
      '500 * 9.81'              → force in Newtons
      '1500 / 3.6'              → km/h to m/s conversion
      'sqrt(2 * 9.81 * 1000)'   → v from kinetic energy formula

    Args:
        expression: A mathematical expression string (Python math syntax).
    """
    return _calculate(expression)


@tool
def memory_recall(query: str, user_id: str) -> str:
    """
    Retrieve relevant information from the user's past DRDO research sessions.
    Use when the question references 'last time', 'earlier', 'previously',
    or when context from past sessions would improve accuracy.

    Args:
        query   : The search query to find relevant memories.
        user_id : The user's ID (from AuthUser).
    """
    return _recall_memory(query, user_id)


@tool
def document_summary(doc_id: str) -> str:
    """
    Return a structured summary of a specific uploaded document from the
    DRDO knowledge base. Extracts: document name, page count, key topics,
    main entities mentioned (labs, weapons, parameters).

    Args:
        doc_id: The document UUID (from the /documents list endpoint).
    """
    return _summarize_document(doc_id, allowed_doc_ids=None)


@tool
def web_search_drdo(query: str) -> str:
    """
    Search for recent public DRDO news, press releases, and announcements
    using DuckDuckGo. Use ONLY for publicly available information.
    Returns top 3 results with title, snippet, and URL.

    Args:
        query: The search query (appends 'DRDO' automatically).
    """
    return _search_web_drdo(query)


@tool
def list_knowledge_base() -> str:
    """
    Return a formatted list of all documents currently in the DRDO
    knowledge base, with their filename, page count, upload date,
    and domain tags.
    """
    return _list_kb(allowed_doc_ids=None)


ALL_TOOLS = [
    drdo_kb_search,
    drdo_calculator,
    memory_recall,
    document_summary,
    web_search_drdo,
    list_knowledge_base,
]


# ── Tool platform: guarded execution (Wave B / B3) ─────────────────────────────

_TOOL_POOL = None
_TOOL_POOL_LOCK = threading.Lock()


def _pool():
    global _TOOL_POOL
    with _TOOL_POOL_LOCK:
        if _TOOL_POOL is None:
            _TOOL_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="tool")
        return _TOOL_POOL


def _run_with_timeout(fn, timeout_s: float):
    """Run fn in a pool thread (context copied so tracing/contextvars carry over)."""
    ctx = contextvars.copy_context()
    fut = _pool().submit(ctx.run, fn)
    try:
        return fut.result(timeout=timeout_s)
    except concurrent.futures.TimeoutError:
        fut.cancel()
        raise TimeoutError(f"tool timed out after {timeout_s:.0f}s")


def guarded_call(tool_id: str, args: dict, fn, *, user_id: str, tenant_id: str = "default",
                 user_role: str = "user", agent_id: str = "react_agent", kind: str = "builtin",
                 description: str = "", schema: Optional[dict] = None) -> str:
    """Every tool call passes: catalog/role policy -> arg-schema validation ->
    circuit breaker -> human approval -> timeout+retry execution -> size cap,
    and is written to the always-on tool ledger with a hash of the args.

    Denials/validation errors are returned as strings (the ReAct loop feeds
    them back to the model, which can then correct the call)."""
    import time as _time
    from VENUAI import approvals, tracing, policy_engine, tool_registry

    ident = types.SimpleNamespace(id=user_id, tenant_id=tenant_id, role=user_role)

    def ledger(status, decision="allowed", approver=None, latency=None, error=None):
        tracing.record_tool_call(tool_id, args, status=status, decision=decision, approver=approver,
                                 latency_ms=latency, error=error, user_id=user_id, tenant_id=tenant_id)

    decision = policy_engine.check_tool_call(user_role=user_role, agent_id=agent_id, tool_id=tool_id)
    if not decision.allowed:
        ledger("denied", "policy_denied", error=decision.reason)
        return f"Tool denied: {decision.reason}"

    desc = tool_registry.get(tool_id)
    schema = schema if schema is not None else (desc.arg_schema if desc else None)
    err = policy_engine.validate_args(schema, args)
    if err:
        ledger("invalid_args", error=err)
        return f"Tool error: {err}. Fix the arguments and call the tool again."

    if not policy_engine.tool_breaker.allow(tool_id):
        ledger("circuit_open", error="circuit open")
        return f"Tool '{tool_id}' is temporarily unavailable after repeated failures; try again later."

    appr = approvals.require_approval(
        tool_id, args, ident, description=description or (desc.description if desc else ""),
        schema=schema, kind=kind)
    if appr.status == "pending":
        ledger("pending_approval", "pending")
        return (f"APPROVAL_REQUIRED: tool '{tool_id}' needs human approval (approval_id={appr.approval_id}). "
                "Tell the user the action is awaiting their approval; do not retry.")
    if appr.status == "denied":
        ledger("denied", "approval_denied", approver=appr.approver)
        return "Tool denied: a human approver rejected this call."

    timeout_s = float(desc.timeout_seconds) if desc else 30.0
    retries = desc.max_retries if desc else 0
    cap = desc.max_result_chars if desc else 20000
    decision_label = "allowed" if appr.approval_id is None else "approved"
    t0 = _time.perf_counter()
    last_err = None
    for attempt in range(retries + 1):
        try:
            with tracing.span(f"tool.{tool_id}", attempt=attempt):
                if os.getenv("TOOL_ENFORCE_TIMEOUT", "on").lower() == "off":
                    out = fn()
                else:
                    out = _run_with_timeout(fn, timeout_s)
            policy_engine.tool_breaker.success(tool_id)
            text = out if isinstance(out, str) else str(out)
            if len(text) > cap:
                text = text[:cap] + f"\n...[truncated {len(text) - cap} chars]"
            ledger("ok", decision_label, appr.approver, (_time.perf_counter() - t0) * 1000)
            return text
        except TimeoutError as e:
            last_err = str(e)
            break                                   # a hung tool is not retried
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            _time.sleep(0.2 * (attempt + 1))
    policy_engine.tool_breaker.failure(tool_id)
    ledger("error", error=last_err, latency=(_time.perf_counter() - t0) * 1000)
    return f"Tool '{tool_id}' failed: {last_err}"


try:  # stateless executor used by approvals.resume once a human approves
    from VENUAI import approvals as _approvals
    _approvals.register_executor("code_execution", lambda a: _run_code(a["code"]))
except Exception:  # pragma: no cover
    pass


# ── Per-request, identity-bound tools ──────────────────────────────────────────

def build_agent_tools(
    user_id: str, allowed_doc_ids: Optional[List[str]], user_role: str = "user",
    collected_docs: Optional[list] = None, tenant_id: Optional[str] = None,
):
    """
    Build a fresh set of tools bound to one request's caller identity.

    `user_id` is bound by closure, not exposed as an LLM-controllable
    parameter. `allowed_doc_ids` (from VENUAI.rag_server.get_visible_doc_ids)
    restricts `drdo_kb_search`, `document_summary`, and `list_knowledge_base`
    to documents the caller is authorized to see.

    Every call goes through `guarded_call`: deny-by-default policy, JSON-schema
    argument validation, circuit breaker, human approval (when TOOL_APPROVAL
    applies), timeout/retry, result-size cap and the tool-call ledger. See
    Documents/AI/tool-policy.md and Documents/AI/agent-protocols.md.

    `tenant_id` scopes approvals/ledger rows; when omitted it is looked up
    from the user record (falling back to "default").

    `collected_docs`, when passed, accumulates every real `Document`
    `drdo_kb_search` retrieves across all tool calls in this request —
    see `_search_kb`'s docstring (Evidence Explorer integration for ReAct).
    """
    if tenant_id is None:
        tenant_id = "default"
        try:
            from VENUAI import auth
            row = auth.get_user_by_id(user_id)
            if row is not None:
                tenant_id = row["tenant_id"]
        except Exception:
            pass

    def _call(tool_id: str, args: dict, fn) -> str:
        return guarded_call(tool_id, args, fn, user_id=user_id, tenant_id=tenant_id, user_role=user_role)

    @tool
    def drdo_kb_search(query: str, top_k: int = 5) -> str:
        """
        Search the DRDO knowledge base using hybrid vector + BM25 retrieval.
        Use this tool for questions about DRDO labs, weapons, projects,
        technical specifications, or research. Returns formatted chunks
        with source citations. Only searches documents you are authorized
        to see.

        Args:
            query : The search query string.
            top_k : Number of results to return (default 5).
        """
        return _call("drdo_kb_search", {"query": query, "top_k": top_k},
                     lambda: _search_kb(query, top_k, allowed_doc_ids=allowed_doc_ids,
                                        collected_docs=collected_docs))

    @tool
    def drdo_calculator(expression: str) -> str:
        """
        Evaluate mathematical and engineering expressions safely.
        Use for range calculations, velocity / energy formulas, and unit conversions.

        Examples:
          '500 * 9.81'              → force in Newtons
          '1500 / 3.6'              → km/h to m/s conversion
          'sqrt(2 * 9.81 * 1000)'   → v from kinetic energy formula

        Args:
            expression: A mathematical expression string (Python math syntax).
        """
        return _call("drdo_calculator", {"expression": expression}, lambda: _calculate(expression))

    @tool
    def memory_recall(query: str) -> str:
        """
        Retrieve relevant information from your own past DRDO research
        sessions. Use when the question references 'last time', 'earlier',
        'previously', or when context from past sessions would improve
        accuracy. Always recalls the current user's own memory — there is
        no way to query another user's memory through this tool.

        Args:
            query : The search query to find relevant memories.
        """
        return _call("memory_recall", {"query": query}, lambda: _recall_memory(query, user_id))

    @tool
    def document_summary(doc_id: str) -> str:
        """
        Return a structured summary of a specific uploaded document from the
        DRDO knowledge base, if you are authorized to see it. Extracts:
        document name, page count, key topics, main entities mentioned
        (labs, weapons, parameters).

        Args:
            doc_id: The document UUID (from the /documents list endpoint).
        """
        return _call("document_summary", {"doc_id": doc_id},
                     lambda: _summarize_document(doc_id, allowed_doc_ids=allowed_doc_ids))

    @tool
    def web_search_drdo(query: str) -> str:
        """
        Search for recent public DRDO news, press releases, and announcements
        using DuckDuckGo. Use ONLY for publicly available information.
        Returns top 3 results with title, snippet, and URL.

        Args:
            query: The search query (appends 'DRDO' automatically).
        """
        return _call("web_search_drdo", {"query": query}, lambda: _search_web_drdo(query))

    @tool
    def list_knowledge_base() -> str:
        """
        Return a formatted list of documents you are authorized to see in
        the DRDO knowledge base, with their filename, page count, upload
        date, and domain tags.
        """
        return _call("list_knowledge_base", {}, lambda: _list_kb(allowed_doc_ids=allowed_doc_ids))

    @tool
    def code_execution(code: str) -> str:
        """
        Run Python code in a real, isolated, network-disabled Docker
        container and return its stdout/stderr. Use for calculations too
        complex for drdo_calculator's expression evaluator (loops, data
        structures, multi-step arithmetic) — never for anything requiring
        network/file access, which this sandbox deliberately blocks.
        Admin-only; needs human approval when TOOL_APPROVAL=on.

        Args:
            code: Python 3 source to execute.
        """
        return _call("code_execution", {"code": code}, lambda: _run_code(code))

    return [
        drdo_kb_search,
        drdo_calculator,
        memory_recall,
        document_summary,
        web_search_drdo,
        list_knowledge_base,
        code_execution,
    ]
