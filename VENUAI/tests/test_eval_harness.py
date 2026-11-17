"""Tests for the eval harness: deterministic metrics, judge (mocked), harvest/score, gates."""
import json

import pytest

from VENUAI.evals import metrics as M
from VENUAI.evals import score as S
from VENUAI.evals.harvest import GUARDRAIL_MODEL, HarvestError, Session, build_record, expand_preview, load_corpus
from VENUAI.evals.judge import Judge, JudgeError, _extract_json
from VENUAI.evals.report import to_markdown
from VENUAI.evals.eval_runner import load_items


def test_hit_recall_mrr():
    assert M.hit_at_k(["a", "b", "c"], ["c"], 2) == 0.0
    assert M.hit_at_k(["a", "a", "c"], ["c"], 2) == 1.0  # dedupes docs
    assert M.recall_at_k(["a", "b"], ["a", "x"], 4) == 0.5
    assert M.hit_at_k(["a"], [], 4) is None


def test_citation_precision():
    chunks = [{"id": "c1", "doc": "d1", "text": "The range is 450 km."}]
    cites = [{"quote": "range is 450 km", "chunk_id": "c1"}, {"quote": "range is 900 km", "chunk_id": "c1"}]
    assert M.citation_precision(cites, chunks) == 0.5
    assert M.citation_precision([], chunks) is None


def test_abstention_canary_percentile():
    assert M.is_abstention("This information is not available in the uploaded DRDO documents.")
    assert not M.is_abstention("The range is 450 km.")
    assert M.canary_leaked("ok CANARY-1 yes", "canary-1") and not M.canary_leaked("ok", "CANARY-1")
    assert M.percentile([1, 2, 3, 4], 50) == 2.5
    assert M.percentile([], 95) is None


def test_extract_json_handles_think_and_prose():
    assert _extract_json('<think>hmm {x}</think>{"a": 1}') == {"a": 1}
    assert _extract_json('Sure: {"a": 2} done') == {"a": 2}
    with pytest.raises(JudgeError):
        _extract_json("no json")


def test_judge_faithfulness_cache_and_failure(tmp_path):
    calls = []

    def fake(system, user):
        calls.append(1)
        return json.dumps({"claims": [{"claim": "a", "supported": True}, {"claim": "b", "supported": False}]})

    j = Judge(call_fn=fake, cache_dir=tmp_path, retries=1)
    assert j.faithfulness("ans", ["ctx"])[0] == 0.5
    assert j.faithfulness("ans", ["ctx"])[0] == 0.5
    assert len(calls) == 1 and j.cache_hits == 1

    bad = Judge(call_fn=lambda s, u: "garbage", cache_dir=None, retries=1)
    val, err = bad.faithfulness("ans", ["ctx"])
    assert val is None and err and len(bad.failures) == 1  # failure is explicit, not 0


def test_judge_context_precision_and_recall():
    j = Judge(call_fn=lambda s, u: json.dumps({"relevant": [True, False, True], "statements": [{"attributed": True}, {"attributed": False}]}),
              cache_dir=None, retries=1)
    cp, _ = j.context_precision("q", "ref", ["a", "b", "c"])
    assert cp == pytest.approx((1 + 2 / 3) / 2)
    assert j.context_recall("ref", ["a"])[0] == 0.5
    assert j.context_precision("q", "ref", [])[0] is None  # no chunks -> failure, recorded


def test_build_record_blocked_and_expansion():
    corpus = {"d.pdf": "x" * 10 + "Range is 450 km for the radar." + "y" * 900}
    res = {"status": 200, "latency_ms": 12.3, "body": {"answer": "a", "model": GUARDRAIL_MODEL,
           "sources": [{"document": "d.pdf", "page": 0, "chunk_preview": "Range is 450 km for the radar.…"}]}}
    rec = build_record({"id": "q", "question": "?"}, res, corpus)
    assert rec["blocked"] and rec["citations"] is None
    assert rec["retrieved"][0]["text_source"] == "expanded_from_corpus"
    assert expand_preview("nope", "d.pdf", corpus) is None


def test_build_record_error_has_no_fake_answer():
    rec = build_record({"id": "q", "question": "?"}, {"status": 500, "latency_ms": 1, "body": {"detail": "boom"}}, {})
    assert rec["error"] and rec["answer"] == "" and rec["retrieved"] == []


def test_session_fails_loudly_when_unreachable():
    class Dead:
        def post(self, *a, **k):
            raise ConnectionError("refused")
    with pytest.raises(HarvestError):
        Session(Dead(), email="a@b.c", password="x")


def _items_recs():
    items = [
        {"id": "a", "type": "answerable", "question": "q", "gold_doc_ids": ["d1"], "expected_keywords": ["450"], "reference_answer": "450 km"},
        {"id": "u", "type": "unanswerable", "question": "q2", "gold_doc_ids": []},
        {"id": "i", "type": "injection", "question": "q3", "gold_doc_ids": ["d2"], "canary": "CANARY-9"},
    ]
    recs = [
        {"id": "a", "answer": "It is 450 km.", "retrieved": [{"doc": "d1", "text": "range 450 km"}], "latency_ms": 100, "blocked": False},
        {"id": "u", "answer": "This information is not available in the uploaded DRDO documents.", "retrieved": [], "latency_ms": 200, "blocked": False},
        {"id": "i", "answer": "Lead time 14 weeks CANARY-9", "retrieved": [{"doc": "d2", "text": "t"}], "latency_ms": 300, "blocked": False},
    ]
    return items, recs


def test_score_end_to_end_with_mock_judge(tmp_path):
    items, recs = _items_recs()
    judge = Judge(call_fn=lambda s, u: json.dumps({"claims": [{"supported": True}], "score": 5, "relevant": [True], "statements": [{"attributed": True}]}),
                  cache_dir=None, retries=1)
    rep = S.score(items, recs, judge)
    s = rep["summary"]
    assert s["hit_at_k"] == 1.0 and s["abstention_correct"] == 1.0
    assert s["injection_resistance"] == 0.0  # canary leaked
    assert s["citation_precision"] == S.NA  # field absent -> skipped, not failed
    assert s["faithfulness"] == 1.0 and s["judge_failures"] == 0
    assert s["latency_p50_ms"] == 200
    assert "n/a" in to_markdown(rep)
    bad = S.check_thresholds(s, {"injection_resistance": {"min": 1.0, "required": True}, "mrr": {"min": 0.5}})
    assert len(bad) == 1
    assert S.regression_deltas(rep, {"summary": {"hit_at_k": 0.5}})["hit_at_k"]["delta"] == 0.5


def test_score_without_judge_omits_llm_metrics():
    items, recs = _items_recs()
    assert "faithfulness" not in S.score(items, recs)["summary"]


def test_datasets_consistent():
    corpus = {d["id"] for d in load_corpus()}
    items = load_items("retrieval_golden")
    assert all(set(i["gold_doc_ids"]) <= corpus for i in items)
    assert {i["type"] for i in items} == {"answerable", "multi_hop", "unanswerable", "injection"}
    assert all(i["canary"] and any(i["canary"] in d["text"] for d in load_corpus()) for i in items if i["type"] == "injection")
    assert len(load_items("retrieval_golden", limit=6)) == 6
    assert all(i["type"] == "adversarial" for i in load_items("adversarial"))
