"""Render score reports as JSON + Markdown + HTML."""
import html
import json
from pathlib import Path
from typing import Optional

LOWER_BETTER = {"latency_p50_ms", "latency_p95_ms", "n_errors", "judge_failures"}


def _f(v):
    return f"{v:.3f}" if isinstance(v, float) else str(v)


def to_markdown(rep: dict, deltas: Optional[dict] = None) -> str:
    s = rep["summary"]
    L = ["# VenuAI evaluation report",
         f"Created {rep['created']} | k={rep['k']} | items={s['n_items']} | errors={s['n_errors']}", "",
         "| Metric | Score | Delta vs previous |", "|---|---|---|"]
    for k, v in s.items():
        if k in ("judge_failure_messages", "n_items"):
            continue
        d = ""
        if deltas and k in deltas:
            x = deltas[k]["delta"]
            bad = (x > 0) if k in LOWER_BETTER else (x < 0)
            d = f"{x:+.3f}" + (" REGRESSION" if bad and abs(x) > 0.02 else "")
        L.append(f"| {k} | {_f(v)} | {d} |")
    if s.get("judge_failure_messages"):
        L += ["", "Judge failures (excluded from means, not scored as 0):"] + [f"- {m}" for m in s["judge_failure_messages"]]
    if rep.get("missing_in_harvest"):
        L += ["", f"Items missing from harvest: {rep['missing_in_harvest']}"]
    L += ["", "## Per-question drill-down", "",
          "| id | type | hit@k | MRR | faith | relev | ctx-P | ctx-R | abstain/canary | ms | answer |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rep["items"]:
        flag = r.get("abstained", r.get("canary_leaked", r.get("adversarial_pass", "")))
        cells = [r["id"], r["type"]]
        for k in ("hit_at_k", "mrr", "faithfulness", "answer_relevancy", "context_precision", "context_recall"):
            cells.append("-" if r.get(k) is None else _f(r[k]))
        cells += [str(flag), str(int(r["latency_ms"])) if r.get("latency_ms") else "-",
                  (r.get("error") or r["answer"]).replace("|", "/").replace("\n", " ")[:110]]
        L.append("| " + " | ".join(cells) + " |")
    return "\n".join(L) + "\n"


def to_html(rep: dict, deltas: Optional[dict] = None) -> str:
    return ("<html><meta charset='utf-8'><body><pre style='font:13px monospace;white-space:pre-wrap'>"
            + html.escape(to_markdown(rep, deltas)) + "</pre></body></html>")


def write_report(rep: dict, out_dir: Path, name: str, deltas=None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    full = dict(rep, deltas=deltas) if deltas else rep
    (out_dir / f"{name}.json").write_text(json.dumps(full, indent=1), encoding="utf-8")
    (out_dir / f"{name}.md").write_text(to_markdown(rep, deltas), encoding="utf-8")
    (out_dir / f"{name}.html").write_text(to_html(rep, deltas), encoding="utf-8")
    return out_dir / f"{name}.json"
