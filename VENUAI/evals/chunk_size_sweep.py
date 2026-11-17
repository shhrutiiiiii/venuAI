"""Chunk-size sweep on the synthetic corpus (local, free: BM25 only, no LLM, no embeddings).

For each chunk size, split every synthetic document, index with the production BM25 index and report
hit@k of the gold document for the answerable / multi-hop golden questions. Lexical-only by design so
it needs no model; it shows the chunking effect on recall, not absolute production quality.

    python -m VENUAI.evals.chunk_size_sweep [--sizes 200,400,800,1200] [--k 4]
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, List, Sequence


def split(text: str, size: int, overlap_ratio: float = 0.15) -> List[str]:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    return RecursiveCharacterTextSplitter(chunk_size=size, chunk_overlap=int(size * overlap_ratio)).split_text(text)


def sweep(corpus: List[dict], items: List[dict], sizes: Sequence[int], k: int = 4) -> dict:
    from VENUAI.retrieval import BM25Index
    qs = [i for i in items if i.get("type") in ("answerable", "multi_hop")]
    rows = []
    for size in sizes:
        texts, metas = [], []
        for d in corpus:
            for ch in split(d["text"], size):
                texts.append(ch)
                metas.append({"doc_id": d["id"]})
        idx = BM25Index(texts, metas)
        hits, full = 0, 0
        for it in qs:
            got = {metas[i]["doc_id"] for i, _s in idx.search(it["question"], k, None)}
            gold = set(it["gold_doc_ids"])
            hits += bool(got & gold)
            full += gold <= got
        rows.append({"chunk_size": size, "n_chunks": len(texts), "n_questions": len(qs),
                     f"any_gold_hit@{k}": round(hits / len(qs), 4) if qs else None,
                     f"all_gold_hit@{k}": round(full / len(qs), 4) if qs else None})
    return {"k": k, "rows": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sizes", default="200,400,800,1200")
    ap.add_argument("--k", type=int, default=4)
    a = ap.parse_args(argv)
    from VENUAI.evals.eval_runner import load_items
    from VENUAI.evals.harvest import load_corpus
    rep = sweep(load_corpus(), load_items("retrieval_golden"), [int(s) for s in a.sizes.split(",")], a.k)
    print(json.dumps(rep, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
