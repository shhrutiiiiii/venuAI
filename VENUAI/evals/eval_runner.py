"""VenuAI evaluation CLI (run from the repo root).

  harvest  run questions through the REAL pipeline -> JSONL (calls the generator LLM)
  score    JSONL + dataset -> metrics report (deterministic; --judge adds LLM judge)
  run      harvest + score
  compare  regression deltas between two JSON reports (oldest first)

Pipeline target: in-process (FastAPI TestClient) by default, or --base-url over HTTP.
There is no offline/fake fallback: an unreachable pipeline is a hard error.

  python -m VENUAI.evals.eval_runner run --base-url http://127.0.0.1:8012 --limit 10 --judge
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from VENUAI.evals import report as R
from VENUAI.evals import score as S
from VENUAI.evals.harvest import DATASETS, HarvestError, harvest

REPORTS_DIR = Path(__file__).parent / "reports"
THRESHOLDS = Path(__file__).parent / "thresholds.json"


def load_items(dataset: str, limit=None):
    p = Path(dataset)
    if not p.exists():
        p = DATASETS / f"{dataset}.json"
    items = json.loads(p.read_text(encoding="utf-8"))
    for i in items:  # adversarial.json items carry 'category' but no 'type'
        if "type" not in i and "category" in i:
            i["type"] = "adversarial"
    if limit:  # stratified round-robin by type so a small run covers every category
        by = {}
        for i in items:
            by.setdefault(i.get("type", "answerable"), []).append(i)
        out = []
        while len(out) < limit and any(by.values()):
            for k in list(by):
                if by[k] and len(out) < limit:
                    out.append(by[k].pop(0))
        items = out
    return items


def make_client(base_url):
    import httpx
    if base_url:
        return httpx.Client(base_url=base_url, timeout=180)
    try:
        from fastapi.testclient import TestClient
        from VENUAI.rag_server import app
    except Exception as e:
        raise HarvestError(f"cannot import the real pipeline in-process: {e}")
    return TestClient(app)


def do_score(items, jsonl, args, name):
    judge = None
    if args.judge:
        from VENUAI.evals.judge import Judge
        judge = Judge()
    rep = S.score(items, S.load_jsonl(jsonl), judge)
    deltas = None
    if args.compare:
        deltas = S.regression_deltas(rep, json.loads(Path(args.compare).read_text(encoding="utf-8")))
    path = R.write_report(rep, Path(args.out_dir), name, deltas)
    print(R.to_markdown(rep, deltas).split("## Per-question")[0])
    print(f"report: {path}")
    if args.gate:
        bad = S.check_thresholds(rep["summary"], json.loads(THRESHOLDS.read_text(encoding="utf-8")))
        for b in bad:
            print("GATE FAIL:", b)
        return 1 if bad else 0
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["harvest", "score", "run", "compare"])
    ap.add_argument("files", nargs="*", help="compare: previous.json current.json")
    ap.add_argument("--dataset", default="retrieval_golden")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--base-url")
    ap.add_argument("--email")
    ap.add_argument("--password")
    ap.add_argument("--top-k", type=int, default=4)
    ap.add_argument("--no-ingest", action="store_true", help="assume the eval corpus is already ingested")
    ap.add_argument("--harvest-file", help="JSONL path (harvest output / score input)")
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--compare", help="previous report JSON for regression deltas")
    ap.add_argument("--gate", action="store_true", help="exit 1 if thresholds.json is violated")
    ap.add_argument("--out-dir", default=str(REPORTS_DIR))
    a = ap.parse_args(argv)

    if a.cmd == "compare":
        prev, cur = (json.loads(Path(f).read_text(encoding="utf-8")) for f in a.files)
        for k, d in S.regression_deltas(cur, prev).items():
            print(f"{k:24s} {d['prev']} -> {d['cur']} ({d['delta']:+})")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    jsonl = Path(a.harvest_file or Path(a.out_dir) / f"harvest_{stamp}.jsonl")
    items = load_items(a.dataset, a.limit)
    try:
        if a.cmd in ("harvest", "run"):
            harvest(make_client(a.base_url), items, jsonl, a.email, a.password, a.top_k, not a.no_ingest)
            print(f"harvest: {jsonl}")
        if a.cmd in ("score", "run"):
            return do_score(items, jsonl, a, f"eval_{stamp}")
    except HarvestError as e:
        print(f"EVAL ABORTED (no report written): {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
