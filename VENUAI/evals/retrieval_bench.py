"""
Offline retrieval benchmark: old heuristic ranking vs the new pipeline.

Builds a TEMPORARY Chroma collection (tempfile dir, deleted afterwards - the
real chroma_db is never opened) from synthetic DRDO-like documents that
contain hard negatives: twin systems whose paragraphs are near-identical
except for the identifier and the numbers (Agni-IV vs Agni-V, Tejas Mk1 vs
Mk1A, ...). All figures are SYNTHETIC test data, not real specifications.

Methods compared (same corpus, same queries, top-5 retrieved):
  old        dense top-k from Chroma, ordered by context_engineer's legacy
             heuristic (50% BM25 / 30% recency / 20% page). This is what
             production effectively ran: the BM25 half of the old hybrid
             needed the uninstalled `rank_bm25` package and silently fell back.
  hybrid     new pipeline without the cross-encoder (dense + BM25 + RRF)
  reranked   new pipeline with the cross-encoder (RERANKER_MODEL)

Reports hit@1, hit@3, MRR over answerable queries, plus the cross-encoder
score distribution for answerable vs out-of-corpus queries (used to calibrate
RETRIEVAL_MIN_SCORE) and the abstention rate at the configured floor.

Run:  python -m VENUAI.evals.retrieval_bench
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("GROQ_API_KEY", "bench-not-used")

from langchain_core.documents import Document  # noqa: E402

# (name, family, range_km, stages, payload, guidance, trial)
SYSTEMS = [
    ("Agni-IV", "Agni", 4000, "two-stage solid", "1000 kg", "ring laser gyro INS with NavIC aiding", "canister launch from a road-mobile launcher, 2014"),
    ("Agni-V", "Agni", 5500, "three-stage solid", "1500 kg", "ring laser gyro INS with NavIC aiding", "canister launch from a rail-mobile launcher, 2012"),
    ("Prithvi-II", "Prithvi", 350, "single-stage liquid", "500 kg", "inertial guidance with strapdown INS", "launch from a mobile launcher, 1996"),
    ("Prithvi-III", "Prithvi", 350, "two-stage solid", "500 kg", "inertial guidance with strapdown INS", "launch from a mobile launcher, 2004"),
    ("Akash Mk1", "Akash", 25, "ramjet sustainer", "60 kg warhead", "command guidance with Rajendra radar", "firing trials at Chandipur, 2008"),
    ("Akash-NG", "Akash", 70, "dual-pulse motor", "60 kg warhead", "active RF seeker with datalink", "firing trials at Chandipur, 2021"),
    ("Astra Mk1", "Astra", 110, "single-stage solid", "15 kg warhead", "active radar seeker with midcourse INS", "launch from Su-30MKI, 2017"),
    ("Astra Mk2", "Astra", 160, "dual-pulse solid", "15 kg warhead", "active radar seeker with midcourse INS", "launch from Su-30MKI, 2022"),
    ("Pinaka Mk1", "Pinaka", 40, "solid rocket motor", "100 kg warhead", "free-flight rocket with spin stabilisation", "salvo of 12 rockets, 2010"),
    ("Pinaka Mk2", "Pinaka", 75, "solid rocket motor", "100 kg warhead", "inertial navigation with GPS correction kit", "salvo of 12 rockets, 2020"),
    ("Tejas Mk1", "Tejas", 1850, "single GE F404 engine", "3500 kg external load", "quadruplex fly-by-wire", "first flight, 2001"),
    ("Tejas Mk1A", "Tejas", 2200, "single GE F404 engine", "3500 kg external load", "quadruplex fly-by-wire with AESA radar", "first flight of the upgraded standard, 2022"),
    ("BrahMos", "BrahMos", 290, "ramjet with solid booster", "200 kg warhead", "inertial navigation with active radar seeker", "test firing from INS Rajput, 2001"),
    ("BrahMos-ER", "BrahMos", 450, "ramjet with solid booster", "200 kg warhead", "inertial navigation with active radar seeker", "test firing from Su-30MKI, 2017"),
    ("Arjun Mk1", "Arjun", 450, "MTU diesel engine", "120 mm rifled gun", "fire-control with laser rangefinder", "user trials, 2004"),
    ("Arjun Mk1A", "Arjun", 450, "MTU diesel engine", "120 mm rifled gun", "fire-control with laser rangefinder and panoramic sight", "user trials, 2021"),
]


def build_corpus() -> Tuple[List[Document], List[Tuple[str, str, str]]]:
    docs: List[Document] = []
    queries: List[Tuple[str, str, str]] = []  # (query, gold_tag, kind)
    for si, (name, fam, rng, stages, payload, guid, trial) in enumerate(SYSTEMS):
        fname = f"{name.replace(' ', '_')}_technical_report.pdf"
        paras = {
            "range": f"{name} specifications. The {fam} class system {name} has a maximum operational range of {rng} km and carries a payload of {payload}.",
            "propulsion": f"{name} propulsion. The {fam} system {name} uses a {stages} arrangement; static tests of the motor were completed before integration.",
            "guidance": f"{name} guidance and control. For {name} the {fam} programme fields {guid}, validated in hardware-in-the-loop trials.",
            "trial": f"{name} flight trials. The {fam} system {name} was demonstrated by {trial}; the telemetry showed nominal performance.",
        }
        for page, (attr, text) in enumerate(paras.items()):
            tag = f"{name}|{attr}"
            docs.append(Document(page_content=text, metadata={
                "doc_id": f"doc-{si}", "filename": fname, "page": page, "chunk_tag": tag,
                "upload_time": f"2025-0{1 + si % 9}-01T00:00:00",
            }))
        # exact-identifier queries (hard: the twin has near-identical text)
        queries.append((f"What is the maximum range of {name}?", f"{name}|range", "exact-id"))
        queries.append((f"How is {name} guided?", f"{name}|guidance", "exact-id"))
        queries.append((f"Describe the propulsion of {name}", f"{name}|propulsion", "exact-id"))
    # paraphrase / value-based queries without the identifier
    queries += [
        ("which missile reaches 5500 km", "Agni-V|range", "paraphrase"),
        ("which system uses a three-stage solid arrangement", "Agni-V|propulsion", "paraphrase"),
        ("which surface-to-air system has a ramjet sustainer", "Akash Mk1|propulsion", "paraphrase"),
        ("which air defence missile has an active RF seeker with datalink", "Akash-NG|guidance", "paraphrase"),
        ("which rocket system uses a GPS correction kit", "Pinaka Mk2|guidance", "paraphrase"),
        ("which fighter has an AESA radar", "Tejas Mk1A|guidance", "paraphrase"),
        ("which cruise missile was fired from INS Rajput", "BrahMos|trial", "paraphrase"),
        ("which tank has a panoramic sight", "Arjun Mk1A|guidance", "paraphrase"),
        ("which air-to-air missile has a 160 km range", "Astra Mk2|range", "paraphrase"),
        ("which ballistic missile uses a single-stage liquid motor", "Prithvi-II|propulsion", "paraphrase"),
    ]
    return docs, queries


OUT_OF_CORPUS = [
    "What is the recipe for chicken biryani?",
    "Who won the cricket world cup in 2011?",
    "What is the capital of Australia?",
    "How do I configure a PostgreSQL replication slot?",
    "Explain the plot of the movie Inception.",
    "What are the symptoms of seasonal influenza?",
]


def _ranks(order: List[str], gold: str) -> int:
    return order.index(gold) + 1 if gold in order else 0


def _summarise(name: str, results: List[int], k: int = 5) -> Dict[str, float]:
    n = len(results)
    return {
        "method": name,
        "hit@1": sum(1 for r in results if r == 1) / n,
        "hit@3": sum(1 for r in results if 1 <= r <= 3) / n,
        f"hit@{k}": sum(1 for r in results if 1 <= r <= k) / n,
        "MRR": sum(1.0 / r for r in results if r) / n,
    }


def main() -> None:
    from langchain_community.embeddings import HuggingFaceEmbeddings
    from langchain_community.vectorstores import Chroma
    from VENUAI import reranker, retrieval
    from VENUAI.context_engineer import rank_chunks

    tmp = tempfile.mkdtemp(prefix="venuai_retrieval_bench_")
    try:
        emb = HuggingFaceEmbeddings(
            model_name=os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
            model_kwargs={"device": "cpu"}, encode_kwargs={"normalize_embeddings": True},
        )
        vs = Chroma(collection_name="bench", embedding_function=emb, persist_directory=tmp)
        docs, queries = build_corpus()
        vs.add_documents(docs)
        retrieval.bump_corpus_version()
        K = 5
        print(f"corpus: {len(docs)} chunks from {len(SYSTEMS)} documents; {len(queries)} answerable + "
              f"{len(OUT_OF_CORPUS)} out-of-corpus queries; temp dir {tmp}")

        # no floors while measuring ranking quality
        os.environ["RETRIEVAL_MIN_SCORE"] = "0"
        os.environ["RETRIEVAL_MIN_SIMILARITY"] = "-1"

        def run_old(q):
            got = vs.as_retriever(search_type="similarity", search_kwargs={"k": K}).invoke(q)
            return [d.metadata["chunk_tag"] for d in rank_chunks(got, q)]

        def run_new(q, rerank):
            os.environ["RERANKER_ENABLED"] = "true" if rerank else "false"
            r = retrieval.retrieve_documents(vs, q, K, None, rewrite=False, include_regions=False)
            return [d.metadata["chunk_tag"] for d in rank_chunks(r.documents, q)]

        reranker.reset_reranker()
        if reranker.get_reranker() is None:
            print("WARNING: reranker failed to load; 'reranked' row will equal 'hybrid'.")
        rows = []
        by_kind: Dict[str, Dict[str, List[int]]] = {}
        for label, fn in (("old", run_old), ("hybrid", lambda q: run_new(q, False)),
                          ("reranked", lambda q: run_new(q, True))):
            ranks = []
            for q, gold, kind in queries:
                r = _ranks(fn(q), gold)
                ranks.append(r)
                by_kind.setdefault(kind, {}).setdefault(label, []).append(r)
            rows.append(_summarise(label, ranks, K))

        print("\nOverall (answerable queries)")
        print(f"{'method':10s} {'hit@1':>7s} {'hit@3':>7s} {'hit@5':>7s} {'MRR':>7s}")
        for r in rows:
            print(f"{r['method']:10s} {r['hit@1']:7.3f} {r['hit@3']:7.3f} {r['hit@5']:7.3f} {r['MRR']:7.3f}")
        for kind, m in by_kind.items():
            print(f"\n{kind} queries (n={len(next(iter(m.values())))})")
            for label, ranks in m.items():
                s = _summarise(label, ranks, K)
                print(f"{label:10s} {s['hit@1']:7.3f} {s['hit@3']:7.3f} {s['hit@5']:7.3f} {s['MRR']:7.3f}")

        # --- relevance-floor calibration -------------------------------------
        os.environ["RERANKER_ENABLED"] = "true"
        top_in, top_out = [], []
        for q, _g, _k in queries:
            r = retrieval.retrieve_documents(vs, q, K, None, rewrite=False, include_regions=False)
            top_in.append(max(s["rerank_score"] for s in r.scores) if r.scores else 0.0)
        for q in OUT_OF_CORPUS:
            r = retrieval.retrieve_documents(vs, q, K, None, rewrite=False, include_regions=False)
            top_out.append(max(s["rerank_score"] for s in r.scores) if r.scores else 0.0)
        top_in.sort(); top_out.sort()
        print("\nTop-1 cross-encoder score (sigmoid)")
        print(f"  answerable   min={top_in[0]:.4f} p10={top_in[len(top_in)//10]:.4f} median={top_in[len(top_in)//2]:.4f}")
        print(f"  out-of-corpus max={top_out[-1]:.4f} median={top_out[len(top_out)//2]:.4f}")

        os.environ.pop("RETRIEVAL_MIN_SCORE")
        os.environ.pop("RETRIEVAL_MIN_SIMILARITY")
        floor = retrieval.min_rerank_score()
        abst_out = sum(retrieval.retrieve_documents(vs, q, K, None, rewrite=False, include_regions=False)
                       .insufficient_context for q in OUT_OF_CORPUS)
        abst_in = sum(retrieval.retrieve_documents(vs, q, K, None, rewrite=False, include_regions=False)
                      .insufficient_context for q, _g, _k in queries)
        print(f"\nAbstention at RETRIEVAL_MIN_SCORE={floor}: out-of-corpus {abst_out}/{len(OUT_OF_CORPUS)} "
              f"abstained (want all); answerable {abst_in}/{len(queries)} wrongly abstained (want 0)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
