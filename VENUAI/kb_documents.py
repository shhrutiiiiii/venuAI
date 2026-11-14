"""Per-entry document representation of the built-in DRDO knowledge base (ADR-B16).

Before: the KB fallback path wrapped ``json.dumps(kb)[:8000]`` into ONE Document, so the
verifier compared answer claims against a raw JSON dump (grounding ~0.1) and nothing could
cite a specific entry. Now every KB entry (organisation, cluster, laboratory, weapon /
platform) becomes its own small, readable, citable Document with a stable id and a title.

Public API (consumed by B1's KB fallback path):

    kb_entry_documents() -> list[Document]
    retrieve_kb_documents(question, k=5) -> list[Document]      # lexical, no model, no network
    kb_fallback_documents(question, k=5, kb_data=None) -> list[Document]   # alias with kb_data override

Metadata of every returned Document: ``filename`` ("Built-in knowledge base (not from your
documents) - <title>"), ``title``, ``kb_entry_id``, ``doc_id`` ("kb:<entry id>"), ``page`` 0,
``kb_fallback`` True, ``retriever`` "kb_lexical" (plus ``kb_score`` when ranked).
These are never user documents: callers must keep labelling the answer as built-in-KB based.
"""
from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.documents import Document

logger = logging.getLogger("drdo-rag")

KB_FILE = Path(__file__).resolve().parent / "data" / "drdo_knowledge_base.json"
# Same literal as answer_pipeline.KB_FILENAME (asserted equal in tests; not imported to keep this
# module free of pipeline imports).
KB_FILENAME = "Built-in knowledge base (not from your documents)"

_STOP = frozenset(
    "a an the of and or to in on for with is are was were be by as at from that this it its what which who "
    "how why when where does do did tell me about give list".split()
)
_cache: Dict[str, Any] = {"mtime": None, "docs": None}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:60] or "entry"


def _render(value: Any, indent: int = 0) -> List[str]:
    pad = "  " * indent
    lines: List[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            label = str(k).replace("_", " ")
            if isinstance(v, (dict, list)):
                lines.append(f"{pad}{label}:")
                lines.extend(_render(v, indent + 1))
            else:
                lines.append(f"{pad}{label}: {v}")
    elif isinstance(value, list):
        if all(not isinstance(v, (dict, list)) for v in value):
            lines.append(f"{pad}" + "; ".join(str(v) for v in value))
        else:
            for v in value:
                lines.extend(_render(v, indent))
    else:
        lines.append(f"{pad}{value}")
    return lines


def _title_of(entry: Dict[str, Any], fallback: str) -> str:
    for key in ("name", "cluster_name", "title"):
        if entry.get(key):
            code = entry.get("code")
            return f"{code} - {entry[key]}" if code and key == "name" else str(entry[key])
    return fallback


def _entries(kb: Dict[str, Any]) -> List[Tuple[str, str, str, str]]:
    """-> [(entry_id, title, kind, text)]"""
    out: List[Tuple[str, str, str, str]] = []
    seen: Dict[str, int] = {}

    def add(kind: str, title: str, body: Any, header: Optional[str] = None) -> None:
        base = f"{kind}-{_slug(title)}"
        n = seen.get(base, 0)
        seen[base] = n + 1
        eid = base if n == 0 else f"{base}-{n + 1}"
        text = "\n".join([f"{title} ({kind})"] + ([header] if header else []) + _render(body))
        out.append((eid, title, kind, text))

    for key, val in kb.items():
        kind = _slug(key).rstrip("s") or "entry"
        if key == "organization" and isinstance(val, dict):
            add("organization", _title_of(val, "DRDO"), val)
        elif key == "clusters" and isinstance(val, list):
            for cl in val:
                if not isinstance(cl, dict):
                    continue
                labs = cl.get("laboratories") if isinstance(cl.get("laboratories"), list) else []
                cname = _title_of(cl, "cluster")
                head = {k: v for k, v in cl.items() if k != "laboratories"}
                lab_codes = ", ".join(str(lb.get("code") or lb.get("name")) for lb in labs if isinstance(lb, dict))
                if lab_codes:
                    head["laboratories_in_cluster"] = lab_codes
                add("cluster", cname, head)
                for lb in labs:
                    if isinstance(lb, dict):
                        add("laboratory", _title_of(lb, "laboratory"), lb, header=f"Part of cluster: {cname}")
        elif isinstance(val, list):
            for i, item in enumerate(val):
                if isinstance(item, dict):
                    add(kind, _title_of(item, f"{key} {i + 1}"), item)
                else:
                    add(kind, f"{key} {i + 1}", item)
        elif isinstance(val, dict):
            add(kind, _title_of(val, str(key)), val)
        else:
            add(kind, str(key), val)
    return out


def _load_kb(kb_data: Optional[dict] = None) -> dict:
    if kb_data is not None:
        return kb_data
    try:
        with open(KB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as err:  # noqa: BLE001
        logger.warning(f"[KB] could not load built-in knowledge base: {err}")
        return {}


def _build_docs(kb: dict) -> List[Document]:
    docs: List[Document] = []
    for eid, title, kind, text in _entries(kb):
        docs.append(Document(
            page_content=text,
            metadata={
                "filename": f"{KB_FILENAME} - {title}", "title": title, "kb_entry_id": eid,
                "kb_kind": kind, "doc_id": f"kb:{eid}", "page": 0, "kb_fallback": True,
                "retriever": "kb_lexical",
            },
        ))
    return docs


def kb_entry_documents(kb_data: Optional[dict] = None) -> List[Document]:
    """All KB entries as Documents (cached by file mtime when reading the bundled file)."""
    if kb_data is not None:
        return _build_docs(kb_data)
    try:
        mtime = KB_FILE.stat().st_mtime
    except OSError:
        return []
    if _cache["docs"] is None or _cache["mtime"] != mtime:
        _cache.update(mtime=mtime, docs=_build_docs(_load_kb()))
    return [Document(page_content=d.page_content, metadata=dict(d.metadata)) for d in _cache["docs"]]


def _tokens(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP and len(t) > 1]


def retrieve_kb_documents(question: str, k: int = 5, kb_data: Optional[dict] = None) -> List[Document]:
    """Top-k KB entries for the question by BM25-style lexical scoring (title hits boosted).
    Returns [] when nothing matches; never pads with irrelevant entries."""
    docs = kb_entry_documents(kb_data)
    q = set(_tokens(question))
    if not docs or not q:
        return []
    toks = [_tokens(d.page_content) for d in docs]
    n = len(docs)
    df: Dict[str, int] = {}
    for tl in toks:
        for t in set(tl):
            df[t] = df.get(t, 0) + 1
    avg = sum(len(t) for t in toks) / n or 1.0
    scored: List[Tuple[float, Document]] = []
    for d, tl in zip(docs, toks):
        title_t = set(_tokens(d.metadata["title"]))
        tf: Dict[str, int] = {}
        for t in tl:
            tf[t] = tf.get(t, 0) + 1
        s = 0.0
        for t in q:
            f = tf.get(t, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * (f * 2.2) / (f + 1.2 * (0.25 + 0.75 * len(tl) / avg))
            if t in title_t:
                s += 1.5 * idf
        if s > 0:
            scored.append((s, d))
    scored.sort(key=lambda p: p[0], reverse=True)
    out = []
    for s, d in scored[:k]:
        d.metadata["kb_score"] = round(s, 4)
        out.append(d)
    return out


def kb_fallback_documents(question: str, k: int = 5, kb_data: Optional[dict] = None) -> List[Document]:
    """What B1's KB fallback should call instead of dumping JSON. Falls back to the organisation
    overview entry when nothing matches lexically so a generic question still has a citable chunk."""
    hits = retrieve_kb_documents(question, k=k, kb_data=kb_data)
    if hits:
        return hits
    docs = kb_entry_documents(kb_data)
    return [d for d in docs if d.metadata.get("kb_kind") == "organization"][:1]
