"""Calibration (reliability) curve for the confidence/grounding signal.

Question answered: when the system reports grounding 0.8, is the answer actually right ~80% of the time?
Input: JSONL rows with a confidence field and a boolean/0-1 correctness field (e.g. harvest + judge output
joined by id). Rows where either value is missing are EXCLUDED and counted (never imputed).

    python -m VENUAI.evals.calibration rows.jsonl --conf grounding_score --correct faithful [--bins 10]

Outputs per-bin mean confidence vs observed accuracy, ECE (expected calibration error) and the Brier
score. With fewer than --min-n usable rows the report says so and gives no summary number.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple


def reliability_curve(pairs: Sequence[Tuple[float, float]], bins: int = 10, min_n: int = 10) -> dict:
    usable = [(float(c), float(y)) for c, y in pairs if c is not None and y is not None]
    out = {"n": len(usable), "bins": [], "ece": None, "brier": None}
    if len(usable) < min_n:
        out["note"] = f"only {len(usable)} usable rows (< {min_n}); no summary computed"
        return out
    edges = [i / bins for i in range(bins + 1)]
    ece = 0.0
    for b in range(bins):
        lo, hi = edges[b], edges[b + 1]
        members = [(c, y) for c, y in usable if (lo <= c < hi) or (b == bins - 1 and c == 1.0)]
        if not members:
            out["bins"].append({"lo": lo, "hi": hi, "n": 0, "mean_confidence": None, "accuracy": None})
            continue
        mc = sum(c for c, _ in members) / len(members)
        acc = sum(y for _, y in members) / len(members)
        ece += len(members) / len(usable) * abs(mc - acc)
        out["bins"].append({"lo": lo, "hi": hi, "n": len(members), "mean_confidence": round(mc, 4),
                            "accuracy": round(acc, 4)})
    out["ece"] = round(ece, 4)
    out["brier"] = round(sum((c - y) ** 2 for c, y in usable) / len(usable), 4)
    return out


def pairs_from_rows(rows: Iterable[dict], conf: str, correct: str) -> Tuple[List[Tuple[float, float]], int]:
    pairs, skipped = [], 0
    for r in rows:
        c, y = r.get(conf), r.get(correct)
        if c is None or y is None:
            skipped += 1
            continue
        pairs.append((float(c), 1.0 if y is True else 0.0 if y is False else float(y)))
    return pairs, skipped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rows")
    ap.add_argument("--conf", default="grounding_score")
    ap.add_argument("--correct", default="faithful")
    ap.add_argument("--bins", type=int, default=10)
    ap.add_argument("--min-n", type=int, default=10)
    a = ap.parse_args(argv)
    rows = [json.loads(line) for line in Path(a.rows).read_text(encoding="utf-8").splitlines() if line.strip()]
    pairs, skipped = pairs_from_rows(rows, a.conf, a.correct)
    rep = reliability_curve(pairs, a.bins, a.min_n)
    rep["skipped_missing"] = skipped
    print(json.dumps(rep, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
