"""B2 eval additions: harvest --resume, calibration, cache sweep, reasoning experiment dry-run, judge key isolation."""
import json

from VENUAI.evals import calibration, reasoning_experiment as rx, semantic_cache_sweep as scs
from VENUAI.evals import harvest as H, judge as J
from VENUAI.evals.eval_runner import load_items


class _Client:
    pass


def test_harvest_resume_skips_done_and_appends(tmp_path, monkeypatch):
    queried = []

    class FakeSession:
        def __init__(self, *a, **k): pass
        def ingest(self, docs): pass
        def cleanup(self): pass
        def query(self, q, top_k=4, extra=None):
            queried.append(q)
            return {"status": 200, "latency_ms": 1.0, "body": {"answer": "a", "sources": []}}

    monkeypatch.setattr(H, "Session", FakeSession)
    monkeypatch.setattr(H.time, "sleep", lambda s: None)
    out = tmp_path / "h.jsonl"
    out.write_text(json.dumps({"id": "a"}) + "\n" + '{"id": "b", "trunc', encoding="utf-8")
    items = [{"id": "a", "question": "qa"}, {"id": "b", "question": "qb"}, {"id": "c", "question": "qc"}]
    H.harvest(_Client(), items, out, resume=True)
    assert queried == ["qb", "qc"]
    ids = [json.loads(l)["id"] for l in out.read_text(encoding="utf-8").splitlines()]
    assert ids == ["a", "b", "c"]


def test_poisoned_docs_expanded_with_exact_canaries():
    corpus = {d["id"]: d["text"] for d in H.load_corpus()}
    inj = [i for i in load_items("retrieval_golden") if i["type"] == "injection"]
    assert len(inj) >= 5
    for i in inj:
        assert i["canary"].startswith("CANARY-") and any(i["canary"] in corpus[g] for g in i["gold_doc_ids"])
    assert len({i["canary"] for i in inj}) == len(inj)


def test_calibration_curve():
    pairs = [(0.9, 1)] * 9 + [(0.9, 0)] + [(0.1, 0)] * 9 + [(0.1, 1)]
    rep = calibration.reliability_curve(pairs, bins=10)
    assert rep["n"] == 20 and rep["ece"] == 0.0 or rep["ece"] < 0.01
    assert rep["brier"] is not None
    small = calibration.reliability_curve([(0.5, 1)], min_n=10)
    assert small["ece"] is None and "note" in small
    pairs2, skipped = calibration.pairs_from_rows([{"c": 0.5, "y": True}, {"c": None, "y": True}], "c", "y")
    assert pairs2 == [(0.5, 1.0)] and skipped == 1


def test_cache_sweep_with_fake_embedder():
    def emb(texts):
        # toy 2-d vectors: identical text -> identical vector; we control similarity by marker
        out = []
        for t in texts:
            out.append([1.0, 0.0] if "PARA" in t or "BASE" in t else [0.0, 1.0] if "OTHER" in t else [0.8, 0.6])
        return out
    pairs = [{"kind": "paraphrase", "a": "BASE x", "b": "PARA x"},
             {"kind": "near_miss", "a": "BASE x", "b": "NEAR x"},
             {"kind": "unrelated", "a": "BASE x", "b": "OTHER x"}]
    rep = scs.sweep(pairs, emb, thresholds=[0.5, 0.9])
    by = {r["threshold"]: r for r in rep["by_threshold"]}
    assert by[0.5]["near_miss_hit_rate"] == 1.0 and by[0.9]["near_miss_hit_rate"] == 0.0
    assert by[0.9]["paraphrase_hit_rate"] == 1.0
    assert rep["recommended_threshold"] == 0.9 and rep["recommended_has_zero_false_hits"]


def test_reasoning_experiment_dry_run_and_budget():
    assert rx.plan(load_items("retrieval_golden"))["max_queries"] == 30
    calls = []

    def q(question, extra):
        calls.append(extra)
        return {"status": 200, "latency_ms": 5, "body": {"answer": "520", "usage": {"total_tokens": 100}}}
    rep = rx.run(load_items("retrieval_golden"), q, max_tokens=250)
    assert rep["tokens_spent"] <= 300 and rep["skipped"]       # stops at the budget, records the rest as skipped
    assert rep["summary"]["standard"]["mean_tokens"] == 100.0


def test_judge_key_isolation(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k1")
    monkeypatch.delenv("EVAL_JUDGE_API_KEY", raising=False)
    assert not J.key_isolation_status()["isolated"] and J.judge_api_key() == "k1"
    monkeypatch.setenv("EVAL_JUDGE_API_KEY", "k2")
    st = J.key_isolation_status()
    assert st["isolated"] and J.judge_api_key() == "k2" and "k2" not in json.dumps(st)
    monkeypatch.setenv("EVAL_JUDGE_API_KEY", "k1")
    assert not J.key_isolation_status()["isolated"]
