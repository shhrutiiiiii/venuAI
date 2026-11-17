"""Reasoning experiment: does extra reasoning (corrective loop / deep depth) improve answers enough to pay for itself?

Runs the same 10 questions through /query in three modes and compares hit@k-style and token cost:
  standard   = {}                                  (cascade default)
  corrective = {"depth": "standard"}               (B1 corrective loop on)
  deep       = {"depth": "deep"}                   (high effort + corrective)
The request field names are B1's contract (``depth``); an older server simply ignores them, and the
report records that no ``corrective`` field came back instead of pretending.

COST SAFETY: dry-run by default (prints the plan and a worst-case call count, makes NO request). Live
runs need ``--live`` and stop when the summed reported ``usage.total_tokens`` exceeds ``--max-tokens``
(default 60000); remaining items are recorded as skipped, never silently dropped.

    python -m VENUAI.evals.reasoning_experiment --base-url http://127.0.0.1:8032 --live --max-tokens 40000
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

MODES: Dict[str, dict] = {"standard": {}, "corrective": {"depth": "standard"}, "deep": {"depth": "deep"}}
N_QUESTIONS = 10


def plan(items: List[dict]) -> dict:
    n = min(N_QUESTIONS, len(items))
    return {"questions": n, "modes": list(MODES), "max_queries": n * len(MODES),
            "note": "each query = 1-4 LLM calls depending on mode; dry run made no requests"}


def run(items: List[dict], query_fn: Callable[[str, dict], dict], max_tokens: int = 60000) -> dict:
    """query_fn(question, extra_payload) -> {"status","latency_ms","body"} (harvest.Session.query shape)."""
    items = [i for i in items if i.get("type") in ("answerable", "multi_hop")][:N_QUESTIONS]
    spent, rows, skipped = 0, [], []
    for mode, extra in MODES.items():
        for it in items:
            if spent >= max_tokens:
                skipped.append({"mode": mode, "id": it["id"], "reason": "token budget reached"})
                continue
            res = query_fn(it["question"], extra)
            body = res.get("body") or {}
            toks = (body.get("usage") or {}).get("total_tokens")
            spent += int(toks or 0)
            answer = (body.get("answer") or "").lower()
            kws = [k.lower() for k in it.get("expected_keywords", [])]
            rows.append({
                "mode": mode, "id": it["id"], "status": res.get("status"),
                "keyword_hit": (any(k in answer for k in kws) if kws else None),
                "tokens": toks, "latency_ms": res.get("latency_ms"),
                "grounding_score": body.get("grounding_score"),
                "corrective_present": "corrective" in body,
            })
    summary = {}
    for mode in MODES:
        r = [x for x in rows if x["mode"] == mode and x["status"] == 200]
        known = [x["tokens"] for x in r if x["tokens"] is not None]
        hits = [x["keyword_hit"] for x in r if x["keyword_hit"] is not None]
        summary[mode] = {
            "n": len(r),
            "keyword_hit_rate": round(sum(hits) / len(hits), 4) if hits else None,
            "mean_tokens": round(sum(known) / len(known), 1) if known else None,
            "mean_latency_ms": round(sum(x["latency_ms"] for x in r) / len(r), 1) if r else None,
        }
    return {"summary": summary, "rows": rows, "skipped": skipped, "tokens_spent": spent}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="actually call the pipeline (spends Groq tokens)")
    ap.add_argument("--base-url")
    ap.add_argument("--email")
    ap.add_argument("--password")
    ap.add_argument("--max-tokens", type=int, default=60000)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    from VENUAI.evals.eval_runner import load_items
    items = load_items("retrieval_golden")
    if not a.live:
        print(json.dumps(plan(items), indent=1))
        print("dry run: pass --live to execute.")
        return 0
    from VENUAI.evals.eval_runner import make_client
    from VENUAI.evals.harvest import Session, load_corpus
    sess = Session(make_client(a.base_url), a.email, a.password)
    try:
        sess.ingest(load_corpus())
        rep = run(items, lambda q, extra: sess.query(q, 4, extra), a.max_tokens)
    finally:
        sess.cleanup()
    print(json.dumps(rep["summary"], indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps(rep, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
