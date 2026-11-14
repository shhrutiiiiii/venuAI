"""Semantic-cache threshold sweep: how often does each cosine threshold wrongly serve a cached answer?

For labelled question pairs it computes, per threshold, the hit rate of
  * paraphrase  (same meaning, SHOULD hit)
  * near_miss   (differs in one decisive entity/number, must NOT hit: a hit is a WRONG ANSWER)
  * unrelated   (must NOT hit)
and recommends the lowest threshold with zero near-miss / unrelated hits (else the one minimising them).

Embeds with the local MiniLM model (free, no Groq tokens, no network once cached). The cache class of
stream B1 (``VENUAI.semantic_cache``) is not required: this measures the embedding geometry that any
cosine-threshold cache inherits; if the module exists its configured threshold is reported for context.

    python -m VENUAI.evals.semantic_cache_sweep [--pairs file.json] [--out report.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

THRESHOLDS = [0.80, 0.85, 0.88, 0.90, 0.92, 0.93, 0.94, 0.95, 0.96, 0.98]

DEFAULT_PAIRS: List[Dict[str, str]] = [
    {"kind": "paraphrase", "a": "What is the maximum detection range of the AURORA-XB radar?",
     "b": "How far can the AURORA-XB radar detect targets at maximum?"},
    {"kind": "paraphrase", "a": "Who supplies the cooling units for AURORA-X?",
     "b": "Which company provides AURORA-X cooling units?"},
    {"kind": "paraphrase", "a": "How long is the radar operator training course?",
     "b": "What is the duration of the radar operator training?"},
    {"kind": "paraphrase", "a": "What is the delivery lead time for the procurement?",
     "b": "How many weeks does delivery take for the procurement?"},
    {"kind": "near_miss", "a": "What is the maximum detection range of the AURORA-XB radar?",
     "b": "What is the maximum detection range of the AURORA-X radar?"},
    {"kind": "near_miss", "a": "What is the range of Agni-V?", "b": "What is the range of Agni-IV?"},
    {"kind": "near_miss", "a": "How long is the radar operator training course?",
     "b": "How long is the sonar operator training course?"},
    {"kind": "near_miss", "a": "What was the lead time in 2023?", "b": "What was the lead time in 2024?"},
    {"kind": "unrelated", "a": "What is the maximum detection range of the AURORA-XB radar?",
     "b": "What is the weight of the Tejas aircraft?"},
    {"kind": "unrelated", "a": "Who supplies the cooling units for AURORA-X?",
     "b": "Where is DRDL located?"},
    {"kind": "unrelated", "a": "How long is the radar operator training course?",
     "b": "Which lab develops composite rocket motor casings?"},
]


def default_embedder() -> Callable[[Sequence[str]], List[List[float]]]:
    from VENUAI.embeddings_config import get_embeddings
    emb = get_embeddings()
    return lambda texts: emb.embed_documents(list(texts))


def cosine(u: Sequence[float], v: Sequence[float]) -> float:
    import math
    dot = sum(x * y for x, y in zip(u, v))
    nu, nv = math.sqrt(sum(x * x for x in u)), math.sqrt(sum(y * y for y in v))
    return dot / (nu * nv) if nu and nv else 0.0


def sweep(pairs: List[Dict[str, str]], embed: Callable, thresholds: Sequence[float] = THRESHOLDS) -> dict:
    texts = [p["a"] for p in pairs] + [p["b"] for p in pairs]
    vecs = embed(texts)
    n = len(pairs)
    sims = [{"kind": p["kind"], "sim": round(cosine(vecs[i], vecs[n + i]), 4)} for i, p in enumerate(pairs)]
    rows = []
    for t in thresholds:
        row: Dict[str, Optional[float]] = {"threshold": t}
        for kind in ("paraphrase", "near_miss", "unrelated"):
            s = [x["sim"] for x in sims if x["kind"] == kind]
            row[f"{kind}_hit_rate"] = round(sum(1 for v in s if v >= t) / len(s), 4) if s else None
        rows.append(row)
    safe = [r for r in rows if not r["near_miss_hit_rate"] and not r["unrelated_hit_rate"]]
    best = (min(safe, key=lambda r: r["threshold"]) if safe else
            min(rows, key=lambda r: ((r["near_miss_hit_rate"] or 0) + (r["unrelated_hit_rate"] or 0), -r["threshold"])))
    return {"n_pairs": n, "pair_similarities": sims, "by_threshold": rows,
            "recommended_threshold": best["threshold"], "recommended_has_zero_false_hits": bool(safe),
            "note": "Small labelled set: treat as a sanity check, extend --pairs with real traffic before tuning."}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pairs", help="JSON list of {kind,a,b}")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    pairs = json.loads(Path(a.pairs).read_text(encoding="utf-8")) if a.pairs else DEFAULT_PAIRS
    rep = sweep(pairs, default_embedder())
    try:
        from VENUAI import semantic_cache  # B1 module, optional
        rep["configured_threshold"] = os.getenv("SEMANTIC_CACHE_THRESHOLD", "0.93") if semantic_cache else None
    except ImportError:
        rep["configured_threshold"] = None
    for r in rep["by_threshold"]:
        print(r)
    print("recommended:", rep["recommended_threshold"], "zero false hits:", rep["recommended_has_zero_false_hits"])
    if a.out:
        Path(a.out).write_text(json.dumps(rep, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
