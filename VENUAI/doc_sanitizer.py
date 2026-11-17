"""
Indirect prompt-injection defence at ingest time (one entry point:
`sanitize_and_scan_chunks`) plus the text primitives reused at prompt-build time.

Chunk text is sanitised (zero-width / bidi / control characters, hidden-text tricks),
scanned with injection patterns and (batched, capped) Prompt Guard, and an
`injection_risk` ("none" | "low" | "medium" | "high") is stored in chunk metadata.
High-risk chunks are quarantined (never shown to the generator) by
context_engineer.build_drdo_prompt.
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, List, Tuple

logger = logging.getLogger("drdo-rag")

# zero-width, bidi controls/isolates, BOM, soft hyphen, word joiner, invisible operators
_INVISIBLE = re.compile(
    "[​-‏‪-‮⁠-⁯﻿­᠎͏￹-￻]"
)
_TAG_CHARS = re.compile("[\U000e0000-\U000e007f]")          # Unicode "tag" smuggling block
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")  # keeps \t \n \r
_HIDDEN_HTML = re.compile(
    r"<[^>]*(?:display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0|color\s*:\s*(?:white|#fff(?:fff)?))[^>]*>.*?</[^>]+>",
    re.I | re.S,
)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)

# Strong = clearly an attempt to instruct the model -> "high" (quarantine).
_STRONG = [
    r"ignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier|preceding)\s+(?:instructions?|prompts?|rules|context)",
    r"disregard\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier|system)\s+(?:instructions?|prompts?|rules)",
    r"forget\s+(?:all\s+|everything\s+)?(?:your|the|previous|prior)\s+(?:instructions?|rules|training)",
    r"(?:new|updated|revised)\s+(?:system\s+)?instructions?\s*:",
    r"^\s*(?:system|assistant)\s*:\s*you\s+(?:are|must|will)",
    r"<\s*/?\s*(?:system|system_role|task_definition|instructions?)\s*>",
    r"\[\s*(?:system|inst)\s*\]|<\|(?:im_start|system|im_end)\|>",
    r"you\s+(?:must|should|will)\s+now\s+(?:ignore|reveal|act|respond|output|say)",
    r"(?:reveal|print|output|repeat|leak)\s+(?:your\s+|the\s+)?(?:system\s+prompt|hidden\s+instructions|api\s+keys?|secrets?)",
    r"you\s+are\s+now\s+(?:dan|an?\s+unrestricted|in\s+developer\s+mode)",
    r"do\s+anything\s+now|developer\s+mode\s+enabled|jailbreak",
    r"when\s+(?:the\s+)?(?:user|assistant|ai|you)\s+(?:reads?|sees?|processes)\s+this",
    r"(?:ai|llm|assistant|model)\s*[,:]?\s*(?:please\s+)?(?:must\s+)?(?:ignore|disregard|do\s+not\s+(?:tell|mention))",
]
# Weak = suspicious phrasing -> "low"
_WEAK = [
    r"\bimportant\s*:\s*(?:instruction|note\s+to\s+(?:ai|assistant|model))",
    r"note\s+to\s+(?:the\s+)?(?:ai|assistant|language\s+model)",
    r"do\s+not\s+(?:tell|inform|reveal\s+to)\s+the\s+user",
    r"instead\s*,?\s+(?:you\s+should|respond\s+with|say|output)",
    r"override\s+(?:all\s+)?(?:safety|security|rules)",
]
_STRONG_RE = [re.compile(p, re.I | re.M) for p in _STRONG]
_WEAK_RE = [re.compile(p, re.I | re.M) for p in _WEAK]

RISK_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}


def sanitize_text(text: str) -> Tuple[str, List[str]]:
    """Strip hidden/invisible content. Returns (clean_text, flags)."""
    flags: List[str] = []
    if not text:
        return text or "", flags
    t = text
    if _TAG_CHARS.search(t):
        flags.append("unicode_tag_chars")
        t = _TAG_CHARS.sub("", t)
    n_inv = len(_INVISIBLE.findall(t))
    if n_inv:
        flags.append("invisible_chars" if n_inv < 8 else "invisible_chars_heavy")
        t = _INVISIBLE.sub("", t)
    if _CONTROL.search(t):
        flags.append("control_chars")
        t = _CONTROL.sub("", t)
    if _HIDDEN_HTML.search(t):
        flags.append("hidden_html")
        t = _HIDDEN_HTML.sub(" ", t)
    if _HTML_COMMENT.search(t):
        flags.append("html_comment")
        t = _HTML_COMMENT.sub(" ", t)
    return t, flags


def scan_injection_patterns(text: str) -> str:
    """Regex-only risk: 'none' | 'low' | 'high'."""
    if any(r.search(text) for r in _STRONG_RE):
        return "high"
    if any(r.search(text) for r in _WEAK_RE):
        return "low"
    return "none"


def assess_text(text: str) -> Tuple[str, str, List[str]]:
    """Sanitise + regex-scan. Returns (clean_text, risk, flags). No network."""
    clean, flags = sanitize_text(text)
    scan_view = unicodedata.normalize("NFKC", clean)  # catches fullwidth/homoglyph-style evasion; stored text unchanged
    risk = scan_injection_patterns(scan_view)
    # Text hidden from human readers is itself suspicious; with a pattern it is high.
    if risk == "none" and ("invisible_chars_heavy" in flags or "unicode_tag_chars" in flags or "hidden_html" in flags):
        risk = "medium"
    elif risk == "none" and flags:
        risk = "low"
    if flags and risk == "high" and scan_injection_patterns(text) != "high":
        flags.append("pattern_hidden_by_obfuscation")
    return clean, risk, flags


def sanitize_and_scan_chunks(chunks: Iterable, use_prompt_guard: bool = True) -> dict:
    """
    ONE call at ingest, just before indexing. Mutates each chunk in place:
    page_content is sanitised; metadata gets injection_risk, injection_flags,
    injection_scan ('regex' | 'regex+prompt_guard'). Returns a summary dict.
    Prompt Guard is batched across a small thread pool and capped by
    INGEST_GUARD_MAX_CHUNKS (default 200); the rest are scanned by regex only.
    """
    from VENUAI import safety_classifiers as sc

    chunks = list(chunks)
    summary = {"total": len(chunks), "high": 0, "medium": 0, "low": 0, "none": 0, "guard_scanned": 0}
    for c in chunks:
        clean, risk, flags = assess_text(c.page_content)
        c.page_content = clean
        c.metadata["injection_risk"] = risk
        c.metadata["injection_flags"] = ",".join(flags)
        c.metadata["injection_scan"] = "regex"

    if use_prompt_guard and sc.prompt_guard_enabled() and chunks:
        cap = int(os.getenv("INGEST_GUARD_MAX_CHUNKS", "200"))
        # Prioritise chunks that already look suspicious, then the rest in order.
        ordered = sorted(chunks, key=lambda c: -RISK_ORDER[c.metadata["injection_risk"]])[:cap]
        with ThreadPoolExecutor(max_workers=min(8, len(ordered))) as pool:
            scores = list(pool.map(lambda c: sc.prompt_guard_score(c.page_content), ordered))
        thr = sc.prompt_guard_threshold()
        for c, s in zip(ordered, scores):
            if s is None:
                continue
            summary["guard_scanned"] += 1
            c.metadata["injection_scan"] = "regex+prompt_guard"
            c.metadata["injection_guard_score"] = round(s, 4)
            cur = c.metadata["injection_risk"]
            new = "high" if s >= max(thr, 0.9) else "medium" if s >= 0.5 else cur
            if RISK_ORDER[new] > RISK_ORDER[cur]:
                c.metadata["injection_risk"] = new

    for c in chunks:
        summary[c.metadata["injection_risk"]] += 1
    if summary["high"] or summary["medium"]:
        logger.warning(f"[ingest-scan] injection risk: {summary}")
    return summary
