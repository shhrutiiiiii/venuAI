"""
Quote-level grounded citations.

The generator (see context_engineer.build_drdo_prompt) cites evidence with numbered
markers [1], [2] that refer to the numbered <untrusted_document> blocks in the prompt,
and ends its answer with a block:

    <citations>
    [1] "short verbatim quote from document 1"
    [2] "short verbatim quote from document 2"
    </citations>

This module (no LLM calls) strips that block from the user-visible answer and
DETERMINISTICALLY verifies each quote is present (whitespace/case/punctuation-
normalised) in the cited chunk. Unverified citations are marked verified=false
with a reason, never silently accepted.
"""
from __future__ import annotations

import hashlib
import re
from typing import Dict, List, Optional, Tuple

CITATION_BLOCK_RE = re.compile(r"<citations>(.*?)(?:</citations>|\Z)", re.S | re.I)
_QUOTE_LINE_RE = re.compile(r"^\s*\[(\d{1,3})\]\s*[:\-]?\s*[\"“'‘`](.+?)[\"”'’`]?\s*$", re.M)
_MARKER_RE = re.compile(r"\[(\d{1,3})\]")

_PUNCT_MAP = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-", " ": " ",
})


def chunk_id_for(doc) -> str:
    m = doc.metadata or {}
    if m.get("chunk_id"):
        return str(m["chunk_id"])
    basis = f"{m.get('doc_id', '')}|{m.get('page', '')}|{doc.page_content}"
    return hashlib.sha1(basis.encode("utf-8", "ignore")).hexdigest()[:12]


def _normalise_with_map(text: str) -> Tuple[str, List[int]]:
    """Lowercase, unify quotes/dashes, collapse whitespace; map each output char -> original index."""
    out: List[str] = []
    idx: List[int] = []
    prev_space = True
    for i, ch in enumerate(text.translate(_PUNCT_MAP)):
        if ch.isspace():
            if prev_space:
                continue
            out.append(" ")
            idx.append(i)
            prev_space = True
        else:
            out.append(ch.lower())
            idx.append(i)
            prev_space = False
    while out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def find_quote(quote: str, chunk_text: str) -> Optional[Tuple[int, int]]:
    """
    Return (start, end) char span of `quote` in chunk_text, or None. A quote may use
    '...' / '…' to skip text: every segment (>= 8 chars) must appear, in order.
    """
    q = quote.translate(_PUNCT_MAP).strip().strip("\"'")
    segments = [s.strip() for s in re.split(r"\.{3,}|…|\[\.\.\.\]", q) if s.strip()]
    segments = [s for s in segments if len(s) >= 8] or ([q] if len(q) >= 8 else [])
    if not segments:
        return None
    norm, idx = _normalise_with_map(chunk_text)
    pos = 0
    first_start = last_end = None
    for seg in segments:
        nseg, _ = _normalise_with_map(seg)
        if not nseg:
            return None
        hit = norm.find(nseg, pos)
        if hit < 0:
            return None
        if first_start is None:
            first_start = idx[hit]
        last_end = idx[hit + len(nseg) - 1] + 1
        pos = hit + len(nseg)
    return first_start, last_end


def extract_citations(raw_answer: str) -> Tuple[str, Dict[int, str]]:
    """Split generator output into (clean_answer, {marker: quote}). Tolerates a missing block."""
    quotes: Dict[int, str] = {}
    m = CITATION_BLOCK_RE.search(raw_answer or "")
    if not m:
        return (raw_answer or "").rstrip(), quotes
    for qm in _QUOTE_LINE_RE.finditer(m.group(1)):
        quotes.setdefault(int(qm.group(1)), qm.group(2).strip())
    clean = (raw_answer[:m.start()] + raw_answer[m.end():]).rstrip()
    return clean, quotes


def verify_citations(clean_answer: str, quotes: Dict[int, str], prompt_docs: List) -> List[dict]:
    """
    prompt_docs[i] is the document shown to the model as [i+1]. Returns one structured
    citation per marker used in the answer or quoted in the citation block.
    """
    used = {int(n) for n in _MARKER_RE.findall(clean_answer or "")} | set(quotes)
    results: List[dict] = []
    for n in sorted(used):
        base = {"index": n, "chunk_id": None, "doc_id": None, "filename": None, "page": None,
                "quote": quotes.get(n), "verified": False, "char_start": None, "char_end": None,
                "reason": None}
        if n < 1 or n > len(prompt_docs):
            base["reason"] = "marker does not refer to a provided document"
            results.append(base)
            continue
        doc = prompt_docs[n - 1]
        meta = doc.metadata or {}
        page = meta.get("page")
        base.update({
            "chunk_id": chunk_id_for(doc),
            "doc_id": meta.get("doc_id"),
            "filename": meta.get("filename"),
            "page": (page + 1) if isinstance(page, int) else None,
        })
        quote = quotes.get(n)
        if not quote:
            base["reason"] = "no supporting quote provided"
        else:
            span = find_quote(quote, doc.page_content)
            if span:
                base.update({"verified": True, "char_start": span[0], "char_end": span[1]})
            else:
                base["reason"] = "quote not found in cited chunk"
        results.append(base)
    return results


def citation_summary(citations: List[dict]) -> dict:
    total = len(citations)
    ok = sum(1 for c in citations if c["verified"])
    return {"total": total, "verified": ok, "unverified": total - ok}


class CitationStreamFilter:
    """Hides the trailing <citations> block from streamed tokens (it is returned structurally)."""

    _OPEN = "<citations>"

    def __init__(self) -> None:
        self._buf = ""
        self._suppress = False

    def feed(self, delta: str) -> str:
        if self._suppress:
            return ""
        self._buf += delta
        i = self._buf.lower().find(self._OPEN)
        if i >= 0:
            out, self._buf, self._suppress = self._buf[:i], "", True
            return out
        # hold back a possible partial tag at the tail
        keep = 0
        for k in range(1, len(self._OPEN)):
            if self._buf.lower().endswith(self._OPEN[:k]):
                keep = k
        out, self._buf = self._buf[:len(self._buf) - keep], self._buf[len(self._buf) - keep:]
        return out

    def flush(self) -> str:
        if self._suppress:
            return ""
        out, self._buf = self._buf, ""
        return out
