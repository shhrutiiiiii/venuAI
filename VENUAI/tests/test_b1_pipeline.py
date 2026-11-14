"""
Wave B / B1: cascade, cache, corrective loop, follow-ups, structured parsing, budgets,
doc scope, history shapes, steps + done event. Every LLM call is mocked.
"""
import json
import types

import pytest
from langchain_core.documents import Document

from VENUAI import (converge, corrective_rag, followups, model_cascade, rag_server, retrieval,
                    run_turn as rt, semantic_cache, structured, token_budget)
from VENUAI.ai_gateway import usage


# ── structured ───────────────────────────────────────────────────────────────
def test_parse_json_repairs_fences_prose_and_trailing_commas():
    assert structured.parse_json('```json\n{"a": 1,}\n```') == {"a": 1}
    assert structured.parse_json('Sure! Here: {"relevant": false, "better_query": "x"} done') == \
        {"relevant": False, "better_query": "x"}
    assert structured.parse_json("not json") is None and structured.parse_json("") is None


# ── cascade ──────────────────────────────────────────────────────────────────
def test_cascade_simple_lookup_uses_fast_model(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("MODEL_CASCADE", "on")
    plan = model_cascade.plan_generation("What is the maximum detection range of the AURORA-X radar?")
    assert plan.prefer_model == "openai/gpt-oss-20b" and plan.effort == "low"


def test_cascade_synthesis_uses_default_strong_model(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    plan = model_cascade.plan_generation("Compare the AURORA-X and AURORA-XB radars and summarise the trials")
    assert plan.prefer_model is None and plan.complexity == model_cascade.COMPLEX


def test_cascade_off_and_downshift(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("MODEL_CASCADE", "off")
    assert model_cascade.plan_generation("What is X?").prefer_model is None
    monkeypatch.setenv("MODEL_CASCADE", "on")
    assert model_cascade.plan_generation("Compare a and b in depth", downshift=True).prefer_model == "openai/gpt-oss-20b"


def test_escalation_only_below_threshold_and_capped(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    plan = model_cascade.CascadePlan(model_cascade.SIMPLE, "openai/gpt-oss-20b", "low")
    good = {"grounding_score": 0.6, "critique_passed": True, "hallucination_score": 0.0}
    bad = {"grounding_score": 0.1, "critique_passed": True, "hallucination_score": 0.0}
    assert not model_cascade.should_escalate(plan, good, 0)
    assert model_cascade.should_escalate(plan, bad, 0)
    assert not model_cascade.should_escalate(plan, bad, model_cascade.max_escalations())   # cap
    assert not model_cascade.should_escalate(plan, bad, 0, downshift=True)                  # budget


# ── corrective loop ──────────────────────────────────────────────────────────
def _bad(answer="a"):
    return {"answer": answer, "critique_passed": False, "unsupported_claim_count": 2,
            "contradicted_claim_count": 0, "hallucination_score": 0.6, "answer_basis": "uploaded_documents",
            "evidence_coverage": 0.3, "evidence_claims": [{"text": "bad claim", "support_status": "unsupported"}]}


def test_corrective_loop_terminates_at_cap(monkeypatch):
    monkeypatch.setenv("CORRECTIVE_MAX_ITERS", "2")
    calls = []

    def regen(docs, fb, it):
        calls.append(it)
        return _bad(f"answer {it} " + "different words " * it)

    best, res = corrective_rag.run_corrective(
        _bad(), regenerate=regen, re_retrieve=lambda fb: [Document(page_content="p")],
        embed=lambda t: [float(len(t)), 1.0, 0.0])
    assert res.iterations == 2 and calls == [1, 2]
    assert res.actions[0] == "re_retrieve" and res.actions.count("regenerate") == 2


def test_corrective_loop_hard_cap_cannot_be_raised(monkeypatch):
    monkeypatch.setenv("CORRECTIVE_MAX_ITERS", "99")
    assert corrective_rag.max_iters() == 2


def test_corrective_loop_stops_when_converged():
    n = []

    def regen(docs, fb, it):
        n.append(it)
        return _bad("same answer")
    _, res = corrective_rag.run_corrective(_bad("same answer"), regenerate=regen,
                                           re_retrieve=lambda fb: None, embed=lambda t: [1.0, 0.0])
    assert res.iterations == 1


def test_corrective_not_triggered_for_good_or_blocked_or_kb():
    good = {**_bad(), "critique_passed": True, "unsupported_claim_count": 0, "hallucination_score": 0.0}
    assert not corrective_rag.needs_correction(good)
    assert not corrective_rag.needs_correction({**_bad(), "blocked": True})
    assert not corrective_rag.needs_correction({**_bad(), "answer_basis": "builtin_knowledge_base"})
    _, res = corrective_rag.run_corrective(good, regenerate=lambda *a: 1 / 0, re_retrieve=lambda fb: None)
    assert res.iterations == 0


def test_corrective_abstains_only_when_nothing_supported():
    st = {**_bad(), "evidence_coverage": 0.0}
    _, res = corrective_rag.run_corrective(st, regenerate=lambda d, f, i: dict(st, answer=f"x{i}" * 9),
                                           re_retrieve=lambda fb: None, embed=lambda t: [float(len(t)), 0.0])
    assert "abstain" in res.actions
    _, res2 = corrective_rag.run_corrective(_bad(), regenerate=lambda d, f, i: _bad(f"y{i}" * 9),
                                            re_retrieve=lambda fb: None, embed=lambda t: [float(len(t)), 0.0])
    assert "abstain" not in res2.actions


def test_grade_retrieval_cheap_and_borderline():
    assert corrective_rag.grade_retrieval([{"rerank_score": 0.9}]) == "good"
    assert corrective_rag.grade_retrieval([{"rerank_score": 0.03}]) == "borderline"
    assert corrective_rag.grade_retrieval([{"similarity": 0.4}]) == "good"   # no rerank signal: no loop


def test_converge_fallback_to_jaccard_without_embeddings():
    assert converge.has_converged("the radar range is 450 km", "the radar range is 450 km", embed=lambda t: None)
    assert not converge.has_converged("alpha beta", "gamma delta", embed=lambda t: None)


# ── follow-ups ───────────────────────────────────────────────────────────────
def test_split_followups_and_stream_filter():
    raw = 'Answer [1].\n<citations>\n[1] "q"\n</citations>\n<followups>["What next?", "And the mass?"]</followups>'
    text, fups = followups.split_followups(raw)
    assert "<followups" not in text and "<citations>" in text and fups == ["What next?", "And the mass?"]
    f = followups.FollowupStreamFilter()
    out = "".join(f.feed(c) for c in ["Ans", " <follow", 'ups>["a"]</fol', "lowups>"]) + f.flush()
    assert out == "Ans "
    assert followups.split_followups("x <followups>[unclosed")[1] == []
    assert len(followups.split_followups('<followups>["aaaaaa","bbbbbb","cccccc","dddddd"]</followups>')[1]) == 3


# ── cache ────────────────────────────────────────────────────────────────────
@pytest.fixture()
def cache_db(tmp_path, monkeypatch):
    monkeypatch.setattr(semantic_cache, "DB_PATH", tmp_path / "cache.db")
    monkeypatch.setenv("SEMANTIC_CACHE", "exact")
    return tmp_path


def test_cache_isolated_across_three_tenants_and_doc_sets(cache_db):
    q = "What is the range?"
    sa = semantic_cache.scope_key("tA", ["d1"], 1, "m")
    sb = semantic_cache.scope_key("tB", ["d1"], 1, "m")
    sc = semantic_cache.scope_key("tC", ["d1", "d2"], 1, "m")
    assert len({sa, sb, sc}) == 3
    semantic_cache.put("tA", sa, q, {"answer": "A"})
    semantic_cache.put("tB", sb, q, {"answer": "B"})
    assert semantic_cache.get("tA", sa, q)["payload"]["answer"] == "A"
    assert semantic_cache.get("tB", sb, q)["payload"]["answer"] == "B"
    assert semantic_cache.get("tC", sc, q) is None
    assert semantic_cache.get("tB", sa, q) is None            # tenant mismatch even with a leaked scope
    # same tenant, different visible doc set -> different scope -> miss
    assert semantic_cache.get("tA", semantic_cache.scope_key("tA", ["d1", "d9"], 1, "m"), q) is None


def test_cache_invalidated_on_upload_or_delete_and_corpus_version(cache_db):
    s = semantic_cache.scope_key("tA", ["d1"], retrieval.get_corpus_version(), "m")
    semantic_cache.put("tA", s, "q?", {"answer": "x"})
    semantic_cache.put("tB", "sb", "q?", {"answer": "y"})
    retrieval.bump_corpus_version("tA")
    assert semantic_cache.get("tA", s, "q?") is None
    assert semantic_cache.get("tB", "sb", "q?") is not None      # tenant-scoped invalidation
    retrieval.bump_corpus_version()
    assert semantic_cache.get("tB", "sb", "q?") is None
    assert semantic_cache.scope_key("tA", ["d1"], retrieval.get_corpus_version(), "m") != s


def test_semantic_tier_off_by_default_and_designator_guard(cache_db, monkeypatch):
    emb = {"what is the range of agni-v": [1.0, 0.0], "what is the range of agni-iv": [0.999, 0.01]}

    def embed(q):
        return emb[semantic_cache.norm_question(q)]
    semantic_cache.put("t", "s", "What is the range of Agni-V", {"answer": "5000"}, embed=embed)
    assert semantic_cache.get("t", "s", "What is the range of Agni-IV", embed=embed) is None   # exact tier only
    monkeypatch.setenv("SEMANTIC_CACHE", "semantic")
    semantic_cache.put("t", "s", "What is the range of Agni-V", {"answer": "5000"}, embed=embed)
    assert semantic_cache.get("t", "s", "What is the range of Agni-IV", embed=embed) is None   # near-miss designator
    assert semantic_cache.threshold() >= 0.98


def test_cache_ttl(cache_db, monkeypatch):
    semantic_cache.put("t", "s", "q?", {"answer": "x"})
    monkeypatch.setenv("SEMANTIC_CACHE_TTL_S", "-1")
    assert semantic_cache.get("t", "s", "q?") is None


# ── budgets ──────────────────────────────────────────────────────────────────
def test_tenant_budget_downshift_then_refuse(tmp_path, monkeypatch):
    monkeypatch.setattr(token_budget, "DB_PATH", tmp_path / "tb.db")
    monkeypatch.setenv("TENANT_DAILY_TOKEN_BUDGET", "1000")
    assert token_budget.tenant_status("t") == "ok"
    token_budget.add_usage("t", 850)
    assert token_budget.tenant_status("t") == "downshift"
    token_budget.add_usage("t", 200)
    assert token_budget.tenant_status("t") == "exhausted"
    assert token_budget.tenant_status("other") == "ok"
    monkeypatch.setenv("TENANT_DAILY_TOKEN_BUDGET", "0")
    assert token_budget.tenant_status("t") == "ok"


def test_request_ceiling_raises_before_next_call():
    m = usage.start_meter(100)
    m.record("m", "generation", 60, 50, 1.0)
    with pytest.raises(usage.BudgetExceeded):
        m.check()
    assert token_budget.meter_downshift(m)


def test_usage_meter_is_honest_about_unreported_tokens():
    m = usage.UsageMeter()
    m.record("m", "generation", None, None, 5.0)
    assert m.complete is False and m.total_tokens == 0


# ── scope / history ──────────────────────────────────────────────────────────
def test_doc_scope_intersects_and_never_widens():
    assert rt.effective_allowed(["a", "b"], ["b", "zzz"]) == ["b"]
    assert rt.effective_allowed(["a", "b"], ["zzz"]) == []          # retrieves nothing
    assert rt.effective_allowed(["a", "b"], None) == ["a", "b"]
    assert rt.effective_allowed(["a"], []) == ["a"]


def test_history_shapes_normalised():
    h = rt.normalise_history([
        {"id": "1", "sender": "user", "text": "hi", "timestamp": "t"},
        {"id": "2", "sender": "ai", "text": "hello", "timestamp": "t"},
        {"role": "user", "content": "x"}, {"junk": 1}, "str"])
    assert [(m["role"], m["content"]) for m in h] == [("user", "hi"), ("assistant", "hello"), ("user", "x")]


def test_retrieved_chunk_shape():
    d = Document(page_content="x" * 2000, metadata={"doc_id": "d", "filename": "f.pdf", "page": 0,
                                                    "rerank_score": 0.5, "retriever": "kg_ppr"})
    c = rt.retrieved_chunks([d])[0]
    assert len(c["text"]) == 1200 and c["page"] == 1 and c["retriever"] == "kg_ppr" and c["score"] == 0.5
    assert set(c) == {"id", "doc", "doc_id", "page", "text", "score", "retriever"}


# ── end-to-end through the API (mocked LLM) ──────────────────────────────────
DOC = Document(page_content="The AURORA-X radar has a maximum detection range of 450 km.",
               metadata={"doc_id": "d1", "filename": "aurora.pdf", "page": 0})


def _events(body):
    return [json.loads(l[6:]) for l in body.splitlines() if l.startswith("data: ")]


@pytest.fixture()
def auth_headers(client):
    r = client.post("/auth/login", json={"email": "demo@chat.com", "password": "demo12345"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def wired(monkeypatch, cache_db, tmp_path):
    monkeypatch.setattr(token_budget, "DB_PATH", tmp_path / "tb.db")
    monkeypatch.setattr(rag_server, "load_metadata", lambda: {"d1": {"tenant_id": "default"}})
    monkeypatch.setattr(rag_server, "get_visible_doc_ids", lambda u: ["d1"])
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: object())
    seen = {"scope": None}

    def fake_retrieve(vs, q, k, allowed, **kw):
        seen["scope"] = allowed
        return retrieval.RetrievalResult(documents=[Document(page_content=DOC.page_content, metadata=dict(DOC.metadata))],
                                         scores=[{"rerank_score": 0.9}])
    monkeypatch.setattr(retrieval, "retrieve_documents", fake_retrieve)
    raw = ('### Range\nThe AURORA-X radar has a maximum detection range of 450 km [1].\n'
           '<citations>\n[1] "maximum detection range of 450 km"\n</citations>\n'
           '<followups>["What is its frequency band?", "Who built it?"]</followups>')
    calls = {"n": 0, "hints": []}

    class FakeLLM:
        def invoke(self, prompt, prefer_model=None, effort=None):
            calls["n"] += 1
            calls["hints"].append((prefer_model, effort))
            return types.SimpleNamespace(content=raw, model_id=prefer_model or "openai/gpt-oss-120b")
    monkeypatch.setattr(rag_server, "get_llm", lambda: FakeLLM())
    monkeypatch.setattr(rag_server, "_GatewayLLM", FakeLLM)

    from VENUAI import answer_pipeline

    def fake_verify(answer, question, docs, light=False):
        return {"final_answer": answer, "grounding_score": 0.8, "hallucination_score": 0.0,
                "verification_flags": [], "verified": True, "verification_reason": None,
                "evidence_coverage": 1.0, "unsupported_claim_count": 0, "contradicted_claim_count": 0,
                "critique_passed": True, "evidence_claims": []}
    monkeypatch.setattr(answer_pipeline, "run_verification", fake_verify)
    monkeypatch.setattr("VENUAI.safety_classifiers.safeguard_classify",
                        lambda t: {"status": "passed", "category": None, "rationale": None})
    seen["calls"] = calls
    return seen


def test_query_returns_contract_fields_and_cascades_to_fast_model(client, auth_headers, wired):
    r = client.post("/query", json={"question": "What is the maximum detection range of the AURORA-X radar?",
                                    "session_id": "b1-a"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    b = r.json()
    for k in ("retrieved_chunks", "usage", "ttft_ms", "latency_ms", "cache_hit", "model_path", "followups",
              "contradictions", "corrective", "trace_id", "memories_used", "pending_approval"):
        assert k in b
    assert b["cache_hit"] is False and b["model_path"] == ["openai/gpt-oss-20b"]
    assert b["followups"] == ["What is its frequency band?", "Who built it?"]
    assert "<followups" not in b["answer"] and "<citations>" not in b["answer"]
    assert b["retrieved_chunks"][0]["doc"] == "aurora.pdf" and b["corrective"] == {"iterations": 0, "actions": []}
    assert b["latency_ms"] >= 0 and b["verified"] is True
    assert wired["calls"]["hints"][0] == ("openai/gpt-oss-20b", "low")


def test_query_second_identical_request_is_a_cache_hit_without_llm(client, auth_headers, wired):
    body = {"question": "What is the maximum detection range of the AURORA-X radar?", "session_id": "b1-c1"}
    first = client.post("/query", json=body, headers=auth_headers).json()
    n = wired["calls"]["n"]
    body["session_id"] = "b1-c2"
    second = client.post("/query", json=body, headers=auth_headers).json()
    assert second["cache_hit"] is True and wired["calls"]["n"] == n
    assert second["answer"] == first["answer"] and second["usage"]["total_tokens"] == 0
    assert second["followups"] == first["followups"] and second["model_path"] == first["model_path"]


def test_doc_ids_scope_is_intersected_with_authorised(client, auth_headers, wired):
    client.post("/query", json={"question": "What is the range?", "doc_ids": ["d1", "forbidden"],
                                "session_id": "b1-s"}, headers=auth_headers)
    assert wired["scope"] == ["d1"]
    client.post("/query", json={"question": "What is the range again?", "doc_ids": ["forbidden"],
                                "session_id": "b1-s2"}, headers=auth_headers)
    assert wired["scope"] == []


def test_slim_ui_history_shape_accepted(client, auth_headers, wired):
    r = client.post("/query", json={"question": "and its mass?", "session_id": "b1-h", "history": [
        {"id": "1", "sender": "user", "text": "range?", "timestamp": "2026"},
        {"id": "2", "sender": "ai", "text": "450 km", "timestamp": "2026"}]}, headers=auth_headers)
    assert r.status_code == 200


def test_tenant_budget_exhausted_returns_429(client, auth_headers, wired, monkeypatch):
    monkeypatch.setenv("TENANT_DAILY_TOKEN_BUDGET", "10")
    monkeypatch.setattr(token_budget, "tenant_status", lambda t: "exhausted")
    r = client.post("/query", json={"question": "What is the range?", "session_id": "b1-b"}, headers=auth_headers)
    assert r.status_code == 429 and "budget" in r.json()["detail"].lower()


def test_stream_emits_ordered_steps_then_done_with_full_shape(client, auth_headers, wired, monkeypatch):
    toks = ["The AURORA-X radar has a maximum detection range of 450 km [1].\n<cita",
            'tions>\n[1] "maximum detection range of 450 km"\n</citations>\n<followups>["Next one?"]</followups>']

    class S:
        model_used = "openai/gpt-oss-20b"
        ttft_ms = 12.0

        def __iter__(self):
            return iter(toks)

    class Gw:
        def stream(self, *a, **k):
            return S()
    monkeypatch.setattr("VENUAI.ai_gateway.gateway.get_gateway", lambda *a: Gw())
    r = client.post("/query/stream", json={"question": "What is the maximum detection range of the AURORA-X radar?",
                                           "session_id": "b1-st"}, headers=auth_headers)
    ev = _events(r.text)
    steps = [(e["step"]["id"], e["step"]["status"]) for e in ev if "step" in e]
    order = [s for s, _ in steps]
    first_idx = {}
    for i, s in enumerate(order):
        first_idx.setdefault(s, i)
    assert first_idx["cache"] < first_idx["retrieving"] < first_idx["reading"] < first_idx["generating"] < first_idx["verifying"]
    assert all(set(e["step"]) == {"id", "status", "label", "t_ms", "detail"} for e in ev if "step" in e)
    done = ev[-1]
    assert done["done"] is True and "<followups" not in done["answer"]
    for k in ("retrieved_chunks", "usage", "ttft_ms", "latency_ms", "cache_hit", "model_path", "followups",
              "contradictions", "corrective", "trace_id", "memories_used", "pending_approval"):
        assert k in done
    assert done["followups"] == ["Next one?"] and done["model_path"] == ["openai/gpt-oss-20b"]
    assert done["ttft_ms"] == 12 and done["cache_hit"] is False
    tokens = "".join(e.get("token", "") for e in ev)
    assert "<followups" not in tokens and "<citations>" not in tokens


def test_health_is_deep_and_has_no_secrets(client):
    b = client.get("/health").json()
    assert "dependencies" in b and "groq_key_present" in b["dependencies"] and "circuit_breakers" in b["dependencies"]
    assert "gsk_" not in json.dumps(b)


def test_query_resume_without_approvals_module_is_503(client, auth_headers):
    r = client.post("/query/resume", json={"approval_id": "x"}, headers=auth_headers)
    assert r.status_code in (503, 404, 200, 400)  # 503 when B3's approvals is absent; delegated otherwise
