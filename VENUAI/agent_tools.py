"""
DRDO VenuAI — Agent Tools
===========================
LangChain @tool-decorated functions for the ReAct agentic loop.
Each tool wraps an existing VenuAI capability and exposes it to the LLM.

Available tools:
  drdo_kb_search      — hybrid vector + BM25 search of the knowledge base
  drdo_calculator     — safe mathematical expression evaluator
  memory_recall       — retrieve relevant facts from the user's past sessions
  document_summary    — structured summary of a specific uploaded document
  web_search_drdo     — DuckDuckGo search for public DRDO news
  list_knowledge_base — list all documents in the knowledge base
"""

import json
import logging
import math
import re
from typing import Optional

from langchain_core.tools import tool

logger = logging.getLogger("drdo-rag")

# ── Safe math namespace for drdo_calculator ────────────────────────────────────
_SAFE_MATH_NS = {
    k: getattr(math, k) for k in dir(math) if not k.startswith("_")
}
_SAFE_MATH_NS.update({"abs": abs, "round": round, "min": min, "max": max, "pow": pow})


# ── Tools ───────────────────────────────────────────────────────────────────────

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
    try:
        from VENUAI.rag_server import get_vector_store
        from VENUAI.context_engineer import rank_chunks

        vs = get_vector_store()
        retriever = vs.as_retriever(
            search_type="similarity",
            search_kwargs={"k": top_k},
        )
        docs = retriever.invoke(query)
        ranked = rank_chunks(docs, query)

        if not ranked:
            return "No relevant documents found in the DRDO knowledge base."

        parts = []
        for i, doc in enumerate(ranked[:top_k], 1):
            meta = doc.metadata
            src = f"[{meta.get('filename', 'KB')}, p.{meta.get('page', 0) + 1}]"
            parts.append(f"**Result {i}** {src}\n{doc.page_content[:400]}")

        return "\n\n---\n\n".join(parts)
    except Exception as e:
        logger.error(f"[drdo_kb_search] Error: {e}")
        return f"Knowledge base search failed: {str(e)}"


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
    # Strip any non-math characters for safety
    safe_expr = re.sub(r"[^0-9\+\-\*/\.\(\)\s\w]", "", expression)
    try:
        result = eval(safe_expr, {"__builtins__": {}}, _SAFE_MATH_NS)  # noqa: S307
        return f"Result: {result}"
    except ZeroDivisionError:
        return "Error: Division by zero."
    except Exception as e:
        return f"Calculation error: {str(e)}. Expression: '{expression}'"


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
    try:
        from VENUAI.memory_manager import get_memory_manager
        mm = get_memory_manager()
        # Use a dummy session_id — we only need semantic + episodic tiers
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


@tool
def document_summary(doc_id: str) -> str:
    """
    Return a structured summary of a specific uploaded document from the
    DRDO knowledge base. Extracts: document name, page count, key topics,
    main entities mentioned (labs, weapons, parameters).

    Args:
        doc_id: The document UUID (from the /documents list endpoint).
    """
    try:
        import json
        from pathlib import Path
        meta_file = Path("VENUAI/doc_metadata.json")
        if not meta_file.exists():
            return "No documents found in the knowledge base."

        with open(meta_file) as f:
            meta = json.load(f)

        if doc_id not in meta:
            return f"Document '{doc_id}' not found. Use list_knowledge_base to see available IDs."

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


@tool
def web_search_drdo(query: str) -> str:
    """
    Search for recent public DRDO news, press releases, and announcements
    using DuckDuckGo. Use ONLY for publicly available information.
    Returns top 3 results with title, snippet, and URL.

    Args:
        query: The search query (appends 'DRDO' automatically).
    """
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddg:
            results = list(ddg.text(f"DRDO {query} site:drdo.gov.in OR site:pib.gov.in", max_results=3))

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


@tool
def list_knowledge_base() -> str:
    """
    Return a formatted list of all documents currently in the DRDO
    knowledge base, with their filename, page count, upload date,
    and domain tags.
    """
    try:
        import json
        from pathlib import Path
        meta_file = Path("VENUAI/doc_metadata.json")
        if not meta_file.exists():
            return "Knowledge base is empty. No documents have been uploaded yet."

        with open(meta_file) as f:
            meta = json.load(f)

        if not meta:
            return "Knowledge base is empty."

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


# ── Tool registry ───────────────────────────────────────────────────────────────
ALL_TOOLS = [
    drdo_kb_search,
    drdo_calculator,
    memory_recall,
    document_summary,
    web_search_drdo,
    list_knowledge_base,
]
