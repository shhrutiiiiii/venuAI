"""B3: tracing, trace store/API isolation, OTel inertness, online evals, A2A."""
import subprocess
import sys
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from VENUAI import a2a_api, auth, online_evals, otel_genai, trace_api, trace_store, tracing


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(trace_store, "DB_PATH", tmp_path / "traces.db")
    monkeypatch.setattr(trace_store, "_ready_for", None)
    monkeypatch.delenv("TRACE_STORE_CONTENT", raising=False)
    monkeypatch.delenv("ONLINE_EVAL_SAMPLE", raising=False)
    tracing._current.set(None)
    yield
    tracing._current.set(None)


def _user(uid, tenant, role="user"):
    return auth.AuthenticatedUser(id=uid, email=f"{uid}@x", role=role, tenant_id=tenant)


def _client(user_holder):
    app = FastAPI()
    app.include_router(trace_api.router)
    app.include_router(a2a_api.router)
    app.dependency_overrides[auth.get_current_user] = lambda: user_holder["u"]
    return TestClient(app)


def _make_trace(tid, tenant, user, **summary):
    tracing.start_trace(tid, tenant, user, "/query")
    tracing.emit_step("retrieving", "start", "Searching")
    tracing.emit_step("retrieving", "end", n=3)
    with tracing.span("generate", model="m"):
        pass
    tracing.record_llm_call("openai/gpt-oss-20b", "generate", 100, 20, 12.5)
    tracing.finish_trace({"question": "secret question", "model_path": ["openai/gpt-oss-20b"], **summary})


def test_noop_without_trace():
    assert tracing.current_trace_id() is None
    tracing.emit_step("x", "start")
    with tracing.span("s"):
        pass
    tracing.record_llm_call("m", "t", 1, 1, 1.0)
    tracing.finish_trace({})
    assert tracing.drain_events() == []


def test_steps_events_and_persist_without_content():
    tracing.start_trace("t1", "ten", "u1", "/query/stream")
    assert tracing.current_trace_id() == "t1"
    tracing.emit_step("retrieving", "start", "Searching 12 passages")
    tracing.emit_step("retrieving", "end", n=12)
    ev = tracing.drain_events()
    assert [e["step"]["status"] for e in ev] == ["start", "end"]
    assert ev[1]["step"]["detail"]["n"] == 12
    assert tracing.drain_events() == []
    tracing.finish_trace({"question": "what is X", "cache_hit": True})
    assert tracing.current_trace_id() is None
    t = trace_store.get_trace("t1", "ten", "u1")
    assert t["steps"][0]["id"] == "retrieving" and t["steps"][0]["status"] == "ok"
    assert t["cache_hit"] is True and t["question_hash"]
    assert "question" not in t  # content off by default


def test_content_opt_in(monkeypatch):
    monkeypatch.setenv("TRACE_STORE_CONTENT", "on")
    _make_trace("t2", "ten", "u1")
    assert trace_store.get_trace("t2", "ten", "u1")["question"] == "secret question"


def test_span_records_error_and_reraises():
    tracing.start_trace("t3", "ten", "u1", "/q")
    with pytest.raises(ValueError):
        with tracing.span("boom"):
            raise ValueError("x")
    assert tracing.active_trace().spans[0]["status"] == "error"


def test_overhead_small():
    tracing.start_trace("t4", "ten", "u1", "/q")
    t0 = time.perf_counter()
    for _ in range(100):
        tracing.emit_step("a", "start")
        with tracing.span("s"):
            pass
    assert (time.perf_counter() - t0) * 1000 / 100 < 2


def test_no_opentelemetry_import_by_default():
    code = ("import os,sys;os.environ.pop('OTEL_EXPORTER_OTLP_ENDPOINT',None);"
            "from VENUAI import tracing,otel_genai,trace_store;"
            "tracing.start_trace('a','t','u','/');tracing.finish_trace({});"
            "assert not any(m.startswith('opentelemetry') for m in sys.modules);"
            "assert otel_genai.export_trace({}) is False")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=str(__import__("pathlib").Path(__file__).parents[2]))
    assert r.returncode == 0, r.stderr[-500:]


def test_otel_span_specs_follow_genai_conventions():
    trace = {"trace_id": "x", "started_at": 1000.0, "total_ms": 500, "path": "/query",
             "llm_calls": [{"model": "m1", "task": "generate", "prompt_tokens": 10,
                            "completion_tokens": 5, "latency_ms": 100, "t_ms": 300}],
             "tool_calls": [{"tool": "drdo_calculator", "status": "ok", "latency_ms": 5}]}
    specs = otel_genai.build_span_specs(trace)
    chat = specs[1]
    assert chat["name"] == "chat m1"
    assert chat["attrs"]["gen_ai.operation.name"] == "chat"
    assert chat["attrs"]["gen_ai.request.model"] == "m1"
    assert chat["attrs"]["gen_ai.usage.input_tokens"] == 10
    assert chat["attrs"]["gen_ai.usage.output_tokens"] == 5
    assert specs[2]["attrs"]["gen_ai.tool.name"] == "drdo_calculator"
    assert otel_genai.GENAI_SEMCONV_VERSION


def test_trace_api_tenant_and_owner_isolation():
    _make_trace("ta", "tenantA", "alice")
    holder = {"u": _user("alice", "tenantA")}
    c = _client(holder)
    assert c.get("/traces/ta").status_code == 200
    assert c.get("/traces/ta").json()["steps"]
    holder["u"] = _user("bob", "tenantA")                     # same tenant, other user
    assert c.get("/traces/ta").status_code == 404
    holder["u"] = _user("mallory", "tenantB", "admin")        # other tenant admin
    assert c.get("/traces/ta").status_code == 404
    assert c.get("/traces").json() == []
    holder["u"] = _user("root", "tenantA", "admin")           # same tenant admin
    assert c.get("/traces/ta").status_code == 200
    assert len(c.get("/traces").json()) == 1


def test_llm_ledger_and_ops_metrics():
    _make_trace("tm", "ten", "u1", cache_hit=False)
    m = trace_store.ops_metrics(7, "ten")
    assert m["llm_calls"] == 1 and m["tokens"]["prompt"] == 100
    assert m["latency_ms"]["p50"] == 12.5
    assert m["cache_hit_rate"] == 0.0


def test_ttl_purge():
    _make_trace("old", "ten", "u1")
    assert trace_store.purge_expired(ttl_seconds=-1) == 1
    assert trace_store.get_trace("old", "ten", "u1") is None


def test_online_eval_default_off_and_sampling(monkeypatch):
    assert online_evals.maybe_evaluate({}, "ans", ["ctx"]) is None
    monkeypatch.setenv("ONLINE_EVAL_SAMPLE", "1.0")
    r = online_evals.maybe_evaluate({}, "The Agni missile has a range of 5000 kilometres.",
                                    ["The Agni missile has a range of 5000 kilometres in total."])
    assert r["method"] in ("lexical_support", "local_faithfulness") and r["score"] >= 0.95
    none = online_evals.maybe_evaluate({}, None, None)
    assert none["score"] is None                         # never a fake number


def test_online_eval_regression_flag(monkeypatch):
    monkeypatch.setenv("ONLINE_EVAL_SAMPLE", "1.0")
    for i in range(6):
        _make_trace(f"oe{i}", "ten", "u1", answer="Completely unrelated sentence about bananas and cheese.",
                    context_chunks=["Radar systems detect aircraft at long range."])
    st = online_evals.status()
    assert st["n"] == 6 and st["online_eval_regression"] is True


# ── A2A ─────────────────────────────────────────────────────────────────────
def test_a2a_disabled_by_default(monkeypatch):
    monkeypatch.delenv("A2A_ENABLED", raising=False)
    c = _client({"u": _user("a", "t")})
    assert c.get("/.well-known/agent-card.json").status_code == 404
    assert c.post("/a2a/v1/message:send", json={"message": {}}).status_code == 404


def test_a2a_card_and_send(monkeypatch):
    monkeypatch.setenv("A2A_ENABLED", "on")
    c = _client({"u": _user("a", "t")})
    card = c.get("/.well-known/agent-card.json").json()
    for k in ("name", "url", "version", "capabilities", "skills", "securitySchemes", "protocolVersion"):
        assert k in card
    assert card["skills"] and all({"id", "name", "description"} <= set(s) for s in card["skills"])
    assert "key" not in str(card).lower() or "api_key" not in str(card).lower()

    async def fake(question, user):
        return {"answer": f"echo:{question}", "agent": "general"}
    monkeypatch.setattr(a2a_api, "answer_via_orchestrator", fake)
    body = {"jsonrpc": "2.0", "id": 7, "method": "message/send",
            "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": "hi"}]}}}
    r = c.post("/a2a/v1/message:send", json=body).json()
    assert r["id"] == 7 and r["result"]["parts"][0]["text"] == "echo:hi"
    bad = c.post("/a2a/v1/message:send", json={"jsonrpc": "2.0", "id": 1, "method": "message/send",
                                                "params": {"message": {"parts": []}}}).json()
    assert bad["error"]["code"] == -32602


def test_a2a_rate_limited(monkeypatch):
    monkeypatch.setenv("A2A_ENABLED", "on")
    monkeypatch.setenv("A2A_RATE_LIMIT_PER_MIN", "2")
    a2a_api._hits.clear()

    async def fake(question, user):
        return {"answer": "ok"}
    monkeypatch.setattr(a2a_api, "answer_via_orchestrator", fake)
    c = _client({"u": _user("rl", "t")})
    msg = {"message": {"parts": [{"text": "q"}]}}
    codes = [c.post("/a2a/v1/message:send", json=msg).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_a2a_requires_auth(monkeypatch):
    monkeypatch.setenv("A2A_ENABLED", "on")
    app = FastAPI()
    app.include_router(a2a_api.router)
    r = TestClient(app).post("/a2a/v1/message:send", json={"message": {"parts": [{"text": "q"}]}})
    assert r.status_code == 401
