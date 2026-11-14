"""
Answer cache (SQLite). Two tiers:

  exact     key = sha256(tenant | sorted allowed doc ids | corpus_version | model-config |
            normalised question). Always safe, on by default.
  semantic  same scope, MiniLM cosine >= SEMANTIC_CACHE_THRESHOLD (default 0.98, floor 0.98) on a
            stored question embedding. OFF by default (see Documents/AI/pipeline.md and
            evals/semantic_cache_sweep.py for the failure modes: near-miss questions with
            different numbers/entities can share an embedding).

SEMANTIC_CACHE = off | exact (default) | semantic.   TTL: SEMANTIC_CACHE_TTL_S (86400).

Isolation: an entry is only ever visible to the same tenant AND the same visible doc-set
AND the same corpus version, so another tenant / user with different documents can never
read it. invalidate() is called from retrieval.bump_corpus_version() on every upload and
delete. The caller decides what is cacheable (never blocked / abstained / unverified
answers, never when memory was used, never with chat history).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence

DB_PATH = Path(os.getenv("SEMANTIC_CACHE_DB", "") or (Path(__file__).parent / "data" / "semantic_cache.db"))
_lock = threading.Lock()


def mode() -> str:
    m = os.getenv("SEMANTIC_CACHE", "exact").strip().lower()
    if m in ("1", "true", "on"):
        return "exact"
    return m if m in ("off", "exact", "semantic") else "exact"


def ttl_seconds() -> float:
    try:
        return float(os.getenv("SEMANTIC_CACHE_TTL_S", "86400"))
    except ValueError:
        return 86400.0


def threshold() -> float:
    try:
        return max(0.98, float(os.getenv("SEMANTIC_CACHE_THRESHOLD", "0.98")))
    except ValueError:
        return 0.98


def norm_question(q: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s.%/-]", "", q.lower())).strip()


_ROMAN = {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"}


def designator_signature(question: str) -> tuple:
    """Tokens that distinguish near-miss questions for an embedding model (MiniLM scores
    Agni-V vs Agni-IV or AURORA-X vs AURORA-XB >= 0.93): anything with a digit or hyphen
    plus roman numerals. A semantic hit must match exactly on this signature."""
    toks = norm_question(question).split()
    return tuple(sorted({t for t in toks if any(c.isdigit() for c in t) or "-" in t or t in _ROMAN}))


def scope_key(tenant_id: str, allowed_doc_ids: Optional[Sequence[str]], corpus_version: int,
              model_cfg: str) -> str:
    docs = "*" if allowed_doc_ids is None else ",".join(sorted(allowed_doc_ids))
    raw = f"{tenant_id}|{docs}|{corpus_version}|{model_cfg}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.execute("CREATE TABLE IF NOT EXISTS answer_cache (id INTEGER PRIMARY KEY AUTOINCREMENT, "
              "tenant_id TEXT NOT NULL, scope TEXT NOT NULL, qhash TEXT NOT NULL, question TEXT, "
              "embedding BLOB, payload TEXT NOT NULL, created REAL NOT NULL)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_cache_scope ON answer_cache(scope, qhash)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_cache_tenant ON answer_cache(tenant_id)")
    return c


def _qhash(question: str) -> str:
    return hashlib.sha256(norm_question(question).encode()).hexdigest()


def _pack(vec: Optional[Sequence[float]]) -> Optional[bytes]:
    if not vec:
        return None
    import numpy as np
    return np.asarray(vec, dtype="float32").tobytes()


def _unpack(b: Optional[bytes]):
    if not b:
        return None
    import numpy as np
    return np.frombuffer(b, dtype="float32")


def get(tenant_id: str, scope: str, question: str,
        embed: Optional[Callable[[str], Optional[List[float]]]] = None) -> Optional[dict]:
    """Return {"payload": dict, "tier": "exact"|"semantic", "similarity": float|None} or None."""
    m = mode()
    if m == "off":
        return None
    cutoff = time.time() - ttl_seconds()
    try:
        with _lock, _conn() as c:
            r = c.execute("SELECT payload FROM answer_cache WHERE scope=? AND qhash=? AND tenant_id=? "
                          "AND created>=? ORDER BY id DESC LIMIT 1",
                          (scope, _qhash(question), tenant_id, cutoff)).fetchone()
            if r:
                return {"payload": json.loads(r[0]), "tier": "exact", "similarity": 1.0}
            if m != "semantic" or embed is None:
                return None
            qv = embed(question)
            if not qv:
                return None
            import numpy as np
            q = np.asarray(qv, dtype="float32")
            qn = float(np.linalg.norm(q)) or 1.0
            best, best_sim = None, -1.0
            for payload, emb, qtext in c.execute("SELECT payload, embedding, question FROM answer_cache WHERE scope=? "
                                          "AND tenant_id=? AND created>=? AND embedding IS NOT NULL",
                                          (scope, tenant_id, cutoff)):
                if designator_signature(qtext or "") != designator_signature(question):
                    continue
                v = _unpack(emb)
                if v is None or v.shape != q.shape:
                    continue
                sim = float(np.dot(q, v) / (qn * (float(np.linalg.norm(v)) or 1.0)))
                if sim > best_sim:
                    best, best_sim = payload, sim
            if best is not None and best_sim >= threshold():
                return {"payload": json.loads(best), "tier": "semantic", "similarity": round(best_sim, 4)}
    except (sqlite3.Error, ValueError):
        return None
    return None


def put(tenant_id: str, scope: str, question: str, payload: dict,
        embed: Optional[Callable[[str], Optional[List[float]]]] = None) -> bool:
    if mode() == "off":
        return False
    vec = None
    if mode() == "semantic" and embed is not None:
        try:
            vec = embed(question)
        except Exception:  # noqa: BLE001
            vec = None
    try:
        with _lock, _conn() as c:
            c.execute("INSERT INTO answer_cache (tenant_id, scope, qhash, question, embedding, payload, created) "
                      "VALUES (?,?,?,?,?,?,?)",
                      (tenant_id, scope, _qhash(question), norm_question(question)[:500], _pack(vec),
                       json.dumps(payload, default=str), time.time()))
        return True
    except sqlite3.Error:
        return False


def invalidate(tenant_id: Optional[str] = None) -> int:
    """Drop a tenant's entries (or everything when tenant_id is None)."""
    try:
        with _lock, _conn() as c:
            cur = c.execute("DELETE FROM answer_cache WHERE tenant_id=?", (tenant_id,)) if tenant_id \
                else c.execute("DELETE FROM answer_cache")
            return cur.rowcount
    except sqlite3.Error:
        return 0
