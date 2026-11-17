"""
Contextual chunking at ingest ("contextual retrieval", Anthropic 2024).

For each chunk a FAST model writes 1-2 sentences situating the chunk within
its document ("This section of the Agni-IV test report covers ..."). The
prefix is stored in chunk.metadata["context_prefix"]; embeddings and the BM25
index use prefix+chunk (VENUAI/retrieval.py), while the displayed / cited
text stays the original chunk.

Cost control: bounded concurrency, a per-document chunk cap, and an abort
after repeated failures (rate limit / outage) - chunks without a prefix are
simply indexed as before.

Env: CONTEXTUAL_CHUNKING (true|false, default true),
     CONTEXTUAL_CHUNKING_CONCURRENCY (4), CONTEXTUAL_CHUNKING_MAX_CHUNKS (200).
"""
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, List, Optional

from langchain_core.documents import Document

logger = logging.getLogger("drdo-rag")

DOC_HEAD_CHARS = 1200
PAGE_CHARS = 1800
MAX_PREFIX_CHARS = 400
ABORT_AFTER_FAILURES = 3


def contextual_chunking_enabled() -> bool:
    return os.getenv("CONTEXTUAL_CHUNKING", "true").strip().lower() not in ("0", "false", "no", "off")


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, "") or default))
    except ValueError:
        return default


def add_context_prefixes(
    chunks: List[Document], raw_pages: List[Document], filename: str,
    complete: Optional[Callable[[str, int], str]] = None,
) -> int:
    """Sets metadata['context_prefix'] in place. Returns number of prefixes written. Never raises."""
    if not contextual_chunking_enabled() or not chunks:
        return 0
    if complete is None:
        from VENUAI.query_rewriter import fast_complete as complete  # type: ignore

    page_text = {(p.metadata or {}).get("page"): p.page_content for p in raw_pages}
    doc_head = (raw_pages[0].page_content if raw_pages else "")[:DOC_HEAD_CHARS]
    limit = _env_int("CONTEXTUAL_CHUNKING_MAX_CHUNKS", 200)
    workers = _env_int("CONTEXTUAL_CHUNKING_CONCURRENCY", 4)
    if len(chunks) > limit:
        logger.info(f"[ContextualChunking] {len(chunks)} chunks > cap {limit}; contextualising the first {limit}.")

    failures = 0
    lock = threading.Lock()
    written = 0

    def work(chunk: Document) -> None:
        nonlocal failures, written
        with lock:
            if failures >= ABORT_AFTER_FAILURES:
                return
        page = (chunk.metadata or {}).get("page")
        prompt = (
            f"Document: {filename}\nDocument beginning:\n{doc_head}\n\n"
            f"Page excerpt:\n{page_text.get(page, '')[:PAGE_CHARS]}\n\n"
            f"Chunk:\n{chunk.page_content}\n\n"
            "Write 1-2 short sentences that situate this chunk within the document (which "
            "document/system/section it belongs to and what it is about) to improve search "
            "retrieval. Keep exact names and model numbers. Reply with the sentences only."
        )
        try:
            text = " ".join(complete(prompt, 100).split())[:MAX_PREFIX_CHARS]
        except Exception as err:
            with lock:
                failures += 1
                if failures == ABORT_AFTER_FAILURES:
                    logger.warning(f"[ContextualChunking] aborting after repeated failures ({err}); remaining chunks unprefixed.")
            return
        if text:
            chunk.metadata["context_prefix"] = text
            with lock:
                failures = 0
                written += 1

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, chunks[:limit]))
    logger.info(f"[ContextualChunking] wrote {written}/{min(len(chunks), limit)} prefixes for {filename}")
    return written
