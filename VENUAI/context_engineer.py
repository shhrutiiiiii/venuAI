"""
DRDO VenuAI — Context Engineer
================================
Maximizes LLM output quality by intelligently managing the context window.

Responsibilities:
  - ContextBudget   : tracks token counts per section, enforces a total budget
  - rank_chunks()   : re-ranks retrieved docs by relevance + recency + page position
  - compress_history(): collapses old turns into a concise summary paragraph
  - build_drdo_prompt(): assembles the final XML-tagged prompt with few-shot examples
"""

import math
import re
import logging
from datetime import datetime, timezone
from typing import List, Optional

from langchain_core.documents import Document

# Epoch-safe sentinel for "no upload_time metadata" — datetime.min.timestamp()
# raises OSError ([Errno 22] Invalid argument) on Windows because year-1 dates
# are outside the platform's representable range. This bit real chunk
# retrieval: /upload only attaches upload_time to the doc_metadata.json entry,
# never to individual chunk metadata in Chroma, so every real retrieved chunk
# hit this fallback. See VENUAI/tests/test_agent_tools_authorization.py and
# VENUAI/tests/test_context_engineer.py.
_EPOCH_FALLBACK = datetime(1970, 1, 1, tzinfo=timezone.utc)

logger = logging.getLogger("drdo-rag")

# ── Token estimation ────────────────────────────────────────────────────────────

def _estimate_tokens(text: str) -> int:
    """Estimate token count using character-based heuristic (chars / 4 ≈ tokens)."""
    return max(1, len(text) // 4)


# ── ContextBudget ───────────────────────────────────────────────────────────────

class ContextBudget:
    """
    Tracks token usage per context section and enforces a total budget.
    Uses character-based estimation (chars/4 ≈ tokens) as a lightweight
    alternative to tiktoken, which avoids an extra dependency.

    Usage:
        budget = ContextBudget()
        safe_content = budget.add_section("evidence", raw_chunk_text)
    """

    MAX_TOKENS: int = 6000  # leave room for the LLM response

    def __init__(self) -> None:
        self.sections: dict[str, int] = {}  # section_name → token_count used

    # ── Public API ────────────────────────────────────────────────────────────

    def add_section(self, name: str, content: str) -> str:
        """
        Register a named section with its content.
        If adding the content would exceed MAX_TOKENS, the content is trimmed
        character-by-character until it fits.

        Args:
            name    : Descriptive label for this section (e.g. "evidence").
            content : Raw text for this section.

        Returns:
            The (possibly trimmed) content that was accepted into the budget.
        """
        tokens_needed = _estimate_tokens(content)
        available = self.remaining()

        if tokens_needed <= available:
            self.sections[name] = tokens_needed
            return content

        # Trim content to fit the remaining budget
        max_chars = available * 4
        trimmed = content[:max_chars].rstrip()
        self.sections[name] = _estimate_tokens(trimmed)
        logger.debug(
            f"[ContextBudget] Section '{name}' trimmed from "
            f"{tokens_needed} → {self.sections[name]} tokens."
        )
        return trimmed

    def remaining(self) -> int:
        """Return the number of tokens still available in the budget."""
        used = sum(self.sections.values())
        return max(0, self.MAX_TOKENS - used)

    def used(self) -> int:
        """Return total tokens consumed so far."""
        return sum(self.sections.values())

    def summary(self) -> str:
        """Human-readable breakdown of token usage per section."""
        lines = [f"  {k}: {v} tokens" for k, v in self.sections.items()]
        lines.append(f"  TOTAL: {self.used()} / {self.MAX_TOKENS}")
        return "\n".join(lines)


# ── Chunk Re-ranking ────────────────────────────────────────────────────────────

def _bm25_score(doc_text: str, query_tokens: List[str], corpus_size: int = 100) -> float:
    """
    Lightweight BM25 approximation without an external library.
    Uses term frequency (TF) and a constant IDF approximation.
    k1=1.5, b=0.75 (standard BM25 parameters).
    """
    k1, b = 1.5, 0.75
    doc_tokens = re.findall(r"\b\w+\b", doc_text.lower())
    doc_len = len(doc_tokens)
    avg_doc_len = 150  # estimated average chunk length

    score = 0.0
    for qt in query_tokens:
        tf = doc_tokens.count(qt)
        idf = math.log((corpus_size - 1 + 0.5) / (tf + 0.5) + 1)
        numerator = tf * (k1 + 1)
        denominator = tf + k1 * (1 - b + b * doc_len / avg_doc_len)
        score += idf * (numerator / denominator)
    return score


def _parse_upload_time(meta: dict) -> datetime:
    """Safely parse upload_time from document metadata."""
    raw = meta.get("upload_time", "")
    if not raw:
        return _EPOCH_FALLBACK
    try:
        parsed = datetime.fromisoformat(raw)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return _EPOCH_FALLBACK


def rank_chunks(docs: List[Document], query: str) -> List[Document]:
    """
    Re-rank retrieved documents by a combined score that weighs:
      1. BM25 keyword relevance (50%)
      2. Recency — newer uploads rank higher (30%)
      3. Page position — earlier pages preferred for spec documents (20%)

    Args:
        docs  : List of LangChain Document objects from the retriever.
        query : The user's question string.

    Returns:
        Documents sorted from highest to lowest combined score.
    """
    if not docs:
        return docs

    query_tokens = re.findall(r"\b\w+\b", query.lower())

    # Collect raw scores
    scored: List[tuple[float, float, float, int, Document]] = []
    upload_times = [_parse_upload_time(d.metadata).timestamp() for d in docs]
    min_ts = min(upload_times) if upload_times else 0
    max_ts = max(upload_times) if upload_times else 1

    for i, doc in enumerate(docs):
        bm25 = _bm25_score(doc.page_content, query_tokens)
        # Normalize recency to [0, 1]
        ts = upload_times[i]
        recency = (ts - min_ts) / (max_ts - min_ts + 1e-9)
        # Page score: lower page number → higher score
        page = doc.metadata.get("page", 99)
        page_score = max(0.0, 1.0 - page / 50.0)
        scored.append((bm25, recency, page_score, i, doc))

    # Normalize BM25 to [0, 1]
    max_bm25 = max(s[0] for s in scored) or 1.0
    ranked = []
    for bm25, recency, page_score, _, doc in scored:
        combined = 0.5 * (bm25 / max_bm25) + 0.3 * recency + 0.2 * page_score
        ranked.append((combined, doc))

    ranked.sort(key=lambda x: x[0], reverse=True)
    logger.debug(f"[rank_chunks] Re-ranked {len(ranked)} chunks.")
    return [doc for _, doc in ranked]


# ── History Compression ─────────────────────────────────────────────────────────

def compress_history(history: List[dict], max_turns: int = 6) -> str:
    """
    If history has more than `max_turns` turns, older turns are compressed
    into a single 2–3 sentence paragraph using extractive summarization
    (no LLM call — uses the first + last sentence of each old turn).

    Args:
        history   : List of dicts with keys 'sender' (or 'role') and 'text' (or 'content').
        max_turns : Number of recent turns to keep verbatim.

    Returns:
        A formatted string ready for prompt injection.
    """
    if not history:
        return "None"

    def _get_field(msg: dict, *keys: str) -> str:
        for k in keys:
            if k in msg:
                return str(msg[k])
        return ""

    recent = history[-max_turns:]
    older = history[:-max_turns] if len(history) > max_turns else []

    summary_parts = []
    if older:
        for msg in older:
            sender = _get_field(msg, "sender", "role")
            text = _get_field(msg, "text", "content")
            if not text:
                continue
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
            if len(sentences) <= 2:
                extract = " ".join(sentences)
            else:
                extract = f"{sentences[0]} … {sentences[-1]}"
            label = "User" if sender in ("user", "human") else "Assistant"
            summary_parts.append(f"{label}: {extract}")

        summary_str = " | ".join(summary_parts)
        summary_block = f"[Earlier conversation summary: {summary_str}]\n\n"
    else:
        summary_block = ""

    # Format recent turns verbatim
    recent_lines = []
    for msg in recent:
        sender = _get_field(msg, "sender", "role")
        text = _get_field(msg, "text", "content")
        label = "User" if sender in ("user", "human") else "Assistant"
        recent_lines.append(f"{label}: {text}")

    return summary_block + "\n".join(recent_lines)


# ── Prompt Assembly ─────────────────────────────────────────────────────────────

_FEW_SHOT_EXAMPLES = """
<example_1>
User question: What are the key specifications of the Pinaka MBRL?
Expected response:
### 🎯 Pinaka Multi-Barrel Rocket Launcher (MBRL)

Developed by **ARDE, Pune**, the Pinaka is India's indigenous area denial artillery system.

| Parameter | Specification |
|-----------|---------------|
| **System Type** | Multi-Barrel Rocket Launcher |
| **Mk-I Range** | 40 km |
| **Guided Pinaka Range** | 75 km |
| **Salvo Time** | 12 rockets in 44 seconds |
| **Target Area** | 3.9 sq km |
| **Developer** | ARDE, Pune (DRDO) |
| **Status** | Operational — Indian Army |

- Fires **high-explosive rockets** in rapid salvos for area saturation.
- Exported to **Armenia** as part of India's defence diplomacy.
- Guided variant uses **GPS/INS** for precision engagement.

*Source: DRDO Armament Systems documentation.*
</example_1>

<example_2>
User question: Which laboratories fall under the Electronics & Communication Systems cluster?
Expected response:
### 📡 Electronics & Communication Systems (ECS) Cluster

The **ECS Cluster** focuses on radar, electronic warfare, and tactical communications.

| Lab Code | Name | Location | Key Focus |
|----------|------|----------|-----------|
| **LRDE** | Electronics & Radar Development Establishment | Bengaluru | Advanced radar, AESA systems |
| **DLRL** | Defence Electronics Research Laboratory | Hyderabad | Electronic Warfare, SIGINT |
| **DEAL** | Defence Electronics Applications Laboratory | Dehradun | SATCOM, Software Defined Radio |

- **LRDE** developed the **Uttam AESA Radar** selected for Tejas Mk-1A.
- **DLRL** is India's centre of excellence for **jamming and SIGINT** systems.
- **DEAL** specializes in **millimeter-wave** and **satellite communication** technologies.
</example_2>
"""


def build_drdo_prompt(
    question: str,
    ranked_docs: List[Document],
    compressed_history: str,
    budget: ContextBudget,
    semantic_memories: Optional[List[str]] = None,
) -> str:
    """
    Build the final DRDO prompt using XML-style section tags ordered by
    LLM attention priority:

        <system_role>         ← who the AI is
        <task_definition>     ← what it must do
        <formatting_rules>    ← output formatting requirements
        <few_shot_examples>   ← 2 ideal DRDO responses
        <user_memory>         ← relevant past interactions (optional)
        <evidence>            ← ranked source chunks (budget-managed)
        <history>             ← compressed conversation history
        <question>            ← the user's question

    Args:
        question           : The user's query.
        ranked_docs        : Re-ranked LangChain Documents from the retriever.
        compressed_history : Output of compress_history().
        budget             : A ContextBudget instance (shared across the call).
        semantic_memories  : Optional list of past memory snippets for personalization.

    Returns:
        A fully assembled prompt string ready for the LLM.
    """
    # ── Fixed sections (not budget-managed) ───────────────────────────────────
    system_role = (
        "You are VenuAI, an advanced, highly knowledgeable AI research assistant for DRDO "
        "(Defence Research and Development Organisation) and scientific computing. "
        "You assist researchers, scientists, engineers, and users with insightful, accurate, and comprehensive answers. "
        "Always answer every question clearly, directly, and helpfully."
    )

    task_definition = (
        "Always answer the user's question directly, clearly, thoroughly, and helpfully. "
        "When relevant evidence or document context is provided in the <evidence> section, ground your answer in that evidence, "
        "cite the document source and page, and provide technical specs or lab parameters in Markdown tables. "
        "If the user asks a question that is conversational, conceptual, general, or not explicitly covered in the evidence, "
        "answer it completely, accurately, and politely using your broad knowledge base. "
        "Never refuse to answer a question or state that you cannot answer."
    )

    formatting_rules = (
        "1. Start with a clear level-3 heading (### ...).\n"
        "2. Present technical specs and lab parameters in Markdown tables (| Col | Col |).\n"
        "3. Use bullet points (- ) for key highlights.\n"
        "4. Bold (**) important lab codes, weapon names, and parameters.\n"
        "5. End with a brief italicised source citation: *Source: [document name, page X]*."
    )

    # ── Budget-managed sections ───────────────────────────────────────────────
    # Reserve tokens for fixed overhead
    overhead_estimate = _estimate_tokens(
        system_role + task_definition + formatting_rules + _FEW_SHOT_EXAMPLES + question
    )
    budget.add_section("overhead", " " * (overhead_estimate * 4))

    # Semantic memories
    memory_block = ""
    if semantic_memories:
        mem_text = "\n".join(f"- {m}" for m in semantic_memories[:3])
        safe_mem = budget.add_section("user_memory", mem_text)
        memory_block = f"\n<user_memory>\n{safe_mem}\n</user_memory>\n"

    # Evidence: inject ranked chunks one by one until budget exhausted
    evidence_parts = []
    for doc in ranked_docs:
        meta = doc.metadata
        src = f"[{meta.get('filename', 'Unknown')}, p.{meta.get('page', 0) + 1}]"
        chunk_text = f"{src}\n{doc.page_content}"
        safe_chunk = budget.add_section(f"chunk_{len(evidence_parts)}", chunk_text)
        evidence_parts.append(safe_chunk)
        if budget.remaining() < 200:
            logger.debug("[build_drdo_prompt] Budget nearly exhausted, stopping chunk injection.")
            break

    evidence_block = "\n\n---\n\n".join(evidence_parts) if evidence_parts else "No documents retrieved."

    # History
    safe_history = budget.add_section("history", compressed_history)

    logger.debug(f"[build_drdo_prompt] Token budget usage:\n{budget.summary()}")

    # ── Assemble ──────────────────────────────────────────────────────────────
    prompt = f"""<system_role>
{system_role}
</system_role>

<task_definition>
{task_definition}
</task_definition>

<formatting_rules>
{formatting_rules}
</formatting_rules>

<few_shot_examples>
{_FEW_SHOT_EXAMPLES.strip()}
</few_shot_examples>
{memory_block}
<evidence>
{evidence_block}
</evidence>

<history>
{safe_history}
</history>

<question>
{question}
</question>

Answer (technical, formatted with headings, tables, bullet points, bold terms, and source citations):"""

    return prompt
