"""HippoRAG-2-style graph retrieval over the existing knowledge graph (Wave B contract 2.3).

    retrieve_graph(query, tenant_id, allowed_doc_ids, k=8, *, vs=None) -> list[Document]

Pipeline: query -> seed entities (name match against the tenant's KG entities; optional 20b NER
only if nothing matched and KG_NER_LLM=on) -> Personalized PageRank (damping 0.5, own numpy power
iteration, no new dependency) over an entity + passage graph -> top passage nodes (documents) ->
best chunks inside those documents -> langchain Documents with metadata ``retriever="kg_ppr"``,
``kg_path`` (seed -> ... -> document), ``kg_score``, ``source``, ``doc_id``.

Graph: nodes are KG entities plus one passage node per source document. A relationship whose
``source_evidence_id`` is ``upload-<doc_id>`` (what /upload auto-population writes) or resolves to a
chunk of a known document links both of its endpoints to that document's passage node; entity-entity
relationships are edges too. Multi-hop reach therefore comes from entities shared between documents.

ACL / tenancy: only the tenant's graph is read; when ``allowed_doc_ids`` is not None an edge is used
only if its document is in that set (edges whose document cannot be resolved are dropped), and the
returned chunks are filtered to allowed documents again. [] allowed means nothing visible.

Intended use (B1): only for queries classified multi-hop, behind KG_RETRIEVAL=on. Returns [] on any
failure (it is an optional retrieval source, never a hard dependency).
"""
from __future__ import annotations

import logging
import os
import re
from collections import deque
from typing import Any, Dict, List, Optional, Set, Tuple

from langchain_core.documents import Document

logger = logging.getLogger("drdo-rag")

DAMPING = 0.5            # HippoRAG-2 uses a high restart probability (0.5)
_TOK = re.compile(r"[a-z0-9]+")
_STOP = frozenset("the of and or a an in on for to is are was what which who how with by from that this".split())


def _tokens(text: str) -> Set[str]:
    return {t for t in _TOK.findall(text.lower()) if t not in _STOP and len(t) > 1}


def personalized_pagerank(
    n: int, edges: List[Tuple[int, int, float]], seeds: Dict[int, float],
    damping: float = DAMPING, iters: int = 100, tol: float = 1e-9,
):
    """Power iteration on an undirected weighted graph. Returns a numpy vector of scores summing to ~1."""
    import numpy as np
    if n == 0 or not seeds:
        return np.zeros(n)
    src = np.array([e[0] for e in edges] + [e[1] for e in edges], dtype=int)
    dst = np.array([e[1] for e in edges] + [e[0] for e in edges], dtype=int)
    w = np.array([e[2] for e in edges] * 2, dtype=float)
    deg = np.zeros(n)
    np.add.at(deg, src, w)
    p0 = np.zeros(n)
    for i, v in seeds.items():
        p0[i] = v
    p0 /= p0.sum()
    p = p0.copy()
    for _ in range(iters):
        contrib = np.zeros(n)
        if len(src):
            np.add.at(contrib, dst, p[src] * w / np.where(deg[src] > 0, deg[src], 1.0))
        dangling = p[deg == 0].sum()      # mass on isolated nodes restarts at the seeds
        new = (1 - damping) * (contrib + dangling * p0) + damping * p0
        if np.abs(new - p).sum() < tol:
            p = new
            break
        p = new
    return p


def _get_store():
    from VENUAI.knowledge_graph.store import get_graph_store
    return get_graph_store()


def _get_vs(vs):
    if vs is not None:
        return vs
    from VENUAI import retrieval
    getter = getattr(retrieval, "get_vectorstore", None)
    if getter is None:
        from VENUAI.rag_server import get_vector_store as getter  # local import: avoid cycle
    return getter()


def _doc_chunks(vs, doc_id: str) -> List[Tuple[str, dict]]:
    try:
        data = vs.get(where={"doc_id": doc_id}, include=["documents", "metadatas"])
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[KG] chunk lookup failed for {doc_id}: {e}")
        return []
    return list(zip((data or {}).get("documents") or [], (data or {}).get("metadatas") or []))


def _ner_llm(query: str) -> List[str]:
    """20b entity extraction fallback (opt-in via KG_NER_LLM=on)."""
    if os.getenv("KG_NER_LLM", "off").lower() not in ("1", "true", "on"):
        return []
    try:
        import json
        from VENUAI.ai_gateway.gateway import get_gateway
        from VENUAI.ai_gateway.model_router import TaskType
        res = get_gateway().complete(
            task_type=TaskType.FAST_CLASSIFICATION, max_tokens=200, temperature=0.0,
            messages=[{"role": "user", "content":
                       'List the named entities (organisations, labs, systems, people) in this question as a JSON '
                       f'array of strings, nothing else.\nQuestion: {query}'}],
        )
        m = re.search(r"\[.*\]", res.content or "", re.S)
        return [str(x) for x in json.loads(m.group(0))][:8] if m else []
    except Exception as e:  # noqa: BLE001
        logger.info(f"[KG] LLM NER unavailable: {e}")
        return []


def _seed_entities(query: str, entities) -> Dict[str, float]:
    """entity_id -> weight. Exact name-in-query beats token-subset match."""
    q_low = query.lower()
    q_tok = _tokens(query)
    seeds: Dict[str, float] = {}
    for e in entities:
        if e.entity_type.value == "document":
            continue
        name = e.name.strip().lower()
        if not name:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", q_low):
            seeds[e.entity_id] = 1.0
        else:
            nt = _tokens(name)
            if nt and nt <= q_tok and any(len(t) >= 3 for t in nt):
                seeds[e.entity_id] = max(seeds.get(e.entity_id, 0.0), 0.5)
    return seeds


def retrieve_graph(
    query: str, tenant_id: str, allowed_doc_ids: Optional[List[str]], k: int = 8, *, vs: Any = None,
    store: Any = None,
) -> List[Document]:
    try:
        return _retrieve_graph(query, tenant_id, allowed_doc_ids, k, vs, store)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[KG] graph retrieval failed, skipping: {type(e).__name__}: {e}")
        return []


def _retrieve_graph(query, tenant_id, allowed_doc_ids, k, vs, store) -> List[Document]:
    if allowed_doc_ids is not None and not allowed_doc_ids:
        return []
    allowed = set(allowed_doc_ids) if allowed_doc_ids is not None else None
    store = store or _get_store()
    entities = store.list_entities(tenant_id=tenant_id)
    rels = store.list_relationships(tenant_id=tenant_id)
    if not entities or not rels:
        return []
    by_id = {e.entity_id: e for e in entities}

    seeds_by_id = _seed_entities(query, entities)
    if not seeds_by_id:
        for name in _ner_llm(query):
            seeds_by_id.update(_seed_entities(name, entities))
    if not seeds_by_id:
        return []

    vs_obj = None
    resolved_cache: Dict[str, Optional[str]] = {}

    def doc_of(evidence_id: Optional[str]) -> Optional[str]:
        if not evidence_id:
            return None
        if evidence_id.startswith("upload-"):
            return evidence_id[len("upload-"):]
        return resolved_cache.get(evidence_id)     # chunk-level evidence ids need a prior resolution

    node_idx: Dict[str, int] = {}

    def node(key: str) -> int:
        return node_idx.setdefault(key, len(node_idx))

    edges: List[Tuple[int, int, float]] = []
    passage_docs: Set[str] = set()
    doc_entity_ids: Set[str] = set()
    for r in rels:
        d = doc_of(r.source_evidence_id)
        if allowed is not None and (d is None or d not in allowed):
            continue
        a, b = by_id.get(r.source_entity_id), by_id.get(r.target_entity_id)
        if a is None or b is None:
            continue
        a_doc, b_doc = a.entity_type.value == "document", b.entity_type.value == "document"
        if not a_doc and not b_doc:
            edges.append((node("e:" + a.entity_id), node("e:" + b.entity_id), 1.0))
        if d is not None:
            passage_docs.add(d)
            for ent, is_doc in ((a, a_doc), (b, b_doc)):
                if is_doc:
                    doc_entity_ids.add(ent.entity_id)
                else:
                    edges.append((node("p:" + d), node("e:" + ent.entity_id), 1.0))
    seeds = {node_idx["e:" + eid]: w for eid, w in seeds_by_id.items() if "e:" + eid in node_idx}
    if not seeds or not passage_docs:
        return []

    scores = personalized_pagerank(len(node_idx), edges, seeds)
    keys = {i: key for key, i in node_idx.items()}
    ranked_docs = sorted(
        ((float(scores[node_idx["p:" + d]]), d) for d in passage_docs if "p:" + d in node_idx), reverse=True,
    )
    ranked_docs = [(s, d) for s, d in ranked_docs if s > 0][: max(3, k)]
    if not ranked_docs:
        return []

    # explainable path: BFS from the nearest seed to each passage node
    adj: Dict[int, List[int]] = {}
    for a, b, _w in edges:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)

    def path_to(target: int) -> List[str]:
        prev = {s: None for s in seeds}
        dq = deque(seeds)
        while dq:
            cur = dq.popleft()
            if cur == target:
                break
            for nb in adj.get(cur, []):
                if nb not in prev:
                    prev[nb] = cur
                    dq.append(nb)
        if target not in prev:
            return []
        out, cur = [], target
        while cur is not None:
            key = keys[cur]
            out.append(by_id[key[2:]].name if key.startswith("e:") else "doc:" + key[2:])
            cur = prev[cur]
        return out[::-1]

    top_entity_terms: Set[str] = set()
    for i in sorted((i for key, i in node_idx.items() if key.startswith("e:")), key=lambda i: -scores[i])[:6]:
        top_entity_terms |= _tokens(by_id[keys[i][2:]].name)
    q_tok = _tokens(query)

    vs_obj = _get_vs(vs)
    out: List[Document] = []
    for score, d in ranked_docs:
        chunks = _doc_chunks(vs_obj, d)
        if not chunks:
            continue
        path = path_to(node_idx["p:" + d])
        scored = []
        for text, meta in chunks:
            t = _tokens(text)
            lex = len(t & q_tok) / (len(q_tok) or 1) + 0.5 * len(t & top_entity_terms) / (len(top_entity_terms) or 1)
            scored.append((lex, text, meta or {}))
        scored.sort(key=lambda x: x[0], reverse=True)
        for lex, text, meta in scored[:2]:
            md = dict(meta)
            if allowed is not None and md.get("doc_id", d) not in allowed:
                continue
            md.update(retriever="kg_ppr", kg_path=path, kg_score=round(score, 6), doc_id=md.get("doc_id", d),
                      source=md.get("filename") or md.get("source") or d)
            out.append(Document(page_content=text, metadata=md))
        if len(out) >= k:
            break
    return out[:k]
