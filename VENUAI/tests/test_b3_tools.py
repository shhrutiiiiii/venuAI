"""B3: tool platform, approvals, MCP pins/client/server alignment, sandbox hook."""
import json
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from VENUAI import agent_tools, approvals, approvals_api, auth, policy_engine, trace_store, tool_registry
import VENUAI.mcp_server as mcp_server
from VENUAI.mcp_client import ExternalMCPClient, MCPClientManager


@pytest.fixture(autouse=True)
def tmp_dbs(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(trace_store, "DB_PATH", tmp_path / "traces.db")
    monkeypatch.setattr(trace_store, "_ready_for", None)
    monkeypatch.setattr(approvals, "DATA_DIR", tmp_path)
    monkeypatch.setattr(approvals, "DB_PATH", tmp_path / "approvals.db")
    monkeypatch.setattr(approvals, "_ready_for", None)
    monkeypatch.delenv("TOOL_APPROVAL", raising=False)
    policy_engine.tool_breaker.reset()
    approvals._PENDING_BY_USER.clear()
    yield


def U(uid="u1", tenant="t1", role="user"):
    return auth.AuthenticatedUser(id=uid, email=f"{uid}@x", role=role, tenant_id=tenant)


# ── arg validation, breaker, guarded_call ───────────────────────────────────
def test_validate_args():
    sch = tool_registry.get("drdo_calculator").arg_schema
    assert policy_engine.validate_args(sch, {"expression": "1+1"}) is None
    assert "expression" in policy_engine.validate_args(sch, {})
    assert policy_engine.validate_args(sch, {"expression": 5}) is not None
    assert policy_engine.validate_args(sch, {"expression": "1", "evil": 1}) is not None


def test_guarded_call_invalid_args_and_ledger_hash():
    out = agent_tools.guarded_call("drdo_calculator", {"expression": 5}, lambda: "never",
                                   user_id="u1", tenant_id="t1")
    assert out.startswith("Tool error: Invalid arguments")
    ok = agent_tools.guarded_call("drdo_calculator", {"expression": "2+2"}, lambda: "Result: 4",
                                  user_id="u1", tenant_id="t1")
    assert ok == "Result: 4"
    rows = trace_store.tool_calls("t1")
    assert {r["status"] for r in rows} == {"ok", "invalid_args"}
    assert all(len(r["args_sha256"]) == 64 for r in rows)
    assert "2+2" not in json.dumps(rows)                 # args never stored raw


def test_guarded_call_policy_denial():
    out = agent_tools.guarded_call("code_execution", {"code": "print(1)"}, lambda: "ran",
                                   user_id="u1", tenant_id="t1", user_role="user")
    assert out.startswith("Tool denied")


def test_retry_then_success_and_result_cap(monkeypatch):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return "x" * 30000
    monkeypatch.setattr(agent_tools, "_run_with_timeout", lambda fn, t: fn())
    out = agent_tools.guarded_call("web_search_drdo", {"query": "q"}, flaky, user_id="u1", tenant_id="t1")
    assert calls["n"] == 2 and "truncated" in out and len(out) < 21000


def test_circuit_breaker_opens(monkeypatch):
    monkeypatch.setattr(agent_tools, "_run_with_timeout", lambda fn, t: fn())

    def boom():
        raise RuntimeError("down")
    for _ in range(3):
        agent_tools.guarded_call("drdo_calculator", {"expression": "1"}, boom, user_id="u", tenant_id="t")
    out = agent_tools.guarded_call("drdo_calculator", {"expression": "1"}, lambda: "ok", user_id="u", tenant_id="t")
    assert "temporarily unavailable" in out


def test_timeout_not_retried(monkeypatch):
    desc = tool_registry.get("drdo_calculator")
    monkeypatch.setattr(tool_registry, "_REGISTRY", {**tool_registry._REGISTRY,
                        "drdo_calculator": type(desc)(**{**desc.__dict__, "timeout_seconds": 0.1, "max_retries": 2})})
    n = {"c": 0}

    def slow():
        n["c"] += 1
        time.sleep(0.5)
        return "late"
    out = agent_tools.guarded_call("drdo_calculator", {"expression": "1"}, slow, user_id="u", tenant_id="t")
    assert "timed out" in out and n["c"] == 1


# ── approvals ───────────────────────────────────────────────────────────────
def test_builtin_no_approval_by_default_but_code_with_on(monkeypatch):
    assert approvals.approval_required("code_execution") is False
    monkeypatch.setenv("TOOL_APPROVAL", "on")
    assert approvals.approval_required("code_execution") is True
    assert approvals.approval_required("drdo_calculator") is False
    monkeypatch.setenv("TOOL_APPROVAL", "off")
    assert approvals.approval_required("x", kind="mcp") is False


def test_approval_flow_pending_approve_run_and_single_use(monkeypatch):
    monkeypatch.setenv("TOOL_APPROVAL", "on")
    monkeypatch.setattr(agent_tools, "_run_with_timeout", lambda fn, t: fn())
    ran = []
    admin = dict(user_id="a1", tenant_id="t1", user_role="admin")
    out = agent_tools.guarded_call("code_execution", {"code": "print(1)"}, lambda: ran.append(1) or "ran", **admin)
    assert out.startswith("APPROVAL_REQUIRED") and not ran
    pend = approvals.pop_pending("a1")
    assert pend[0]["tool"] == "code_execution" and len(pend[0]["description_hash"]) == 64
    aid = pend[0]["id"]
    assert approvals.decide(aid, U("a1", "t1", "admin"), True)["status"] == "approved"
    assert agent_tools.guarded_call("code_execution", {"code": "print(1)"}, lambda: ran.append(1) or "ran", **admin) == "ran"
    # single use: the same call needs a fresh approval
    again = agent_tools.guarded_call("code_execution", {"code": "print(1)"}, lambda: "ran", **admin)
    assert again.startswith("APPROVAL_REQUIRED")
    ledger = trace_store.tool_calls("t1")
    assert any(r["decision"] == "approved" for r in ledger)


def test_denied_and_resume(monkeypatch):
    monkeypatch.setenv("TOOL_APPROVAL", "on")
    ad = U("a1", "t1", "admin")
    d = approvals.require_approval("code_execution", {"code": "x"}, ad)
    approvals.decide(d.approval_id, ad, False)
    assert approvals.require_approval("code_execution", {"code": "x"}, ad).status == "denied"
    d2 = approvals.require_approval("code_execution", {"code": "y"}, ad)
    assert approvals.resume(d2.approval_id, ad)["status"] == "not_approved"
    approvals.decide(d2.approval_id, ad, True)
    approvals.register_executor("code_execution", lambda a: f"ran {a['code']}")
    r = approvals.resume(d2.approval_id, ad)
    assert r == {"status": "completed", "tool": "code_execution", "result": "ran y"}
    assert approvals.resume(d2.approval_id, ad)["status"] == "already_used"


def test_approval_tenant_isolation_and_api(monkeypatch):
    monkeypatch.setenv("TOOL_APPROVAL", "on")
    owner = U("a1", "t1", "admin")
    d = approvals.require_approval("code_execution", {"code": "x"}, owner)
    holder = {"u": U("evil", "t2", "admin")}
    app = FastAPI()
    app.include_router(approvals_api.router)
    app.dependency_overrides[auth.get_current_user] = lambda: holder["u"]
    c = TestClient(app)
    assert c.get("/approvals/pending").json() == []
    assert c.post(f"/approvals/{d.approval_id}/decision", json={"approve": True}).status_code == 404
    holder["u"] = owner
    assert len(c.get("/approvals/pending").json()) == 1
    r = c.post(f"/approvals/{d.approval_id}/decision", json={"approve": True, "remember": True})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    # remember => later calls with different args are allowed for the same tool definition
    assert approvals.require_approval("code_execution", {"code": "other"}, owner).status == "allowed"


def test_pin_drift_blocks_and_reapproval():
    assert approvals.pin_tool("srv", "t", "desc", {"type": "object"}) == "new"
    assert approvals.pin_tool("srv", "t", "desc", {"type": "object"}) == "ok"
    assert approvals.pin_tool("srv", "t", "desc EVIL", {"type": "object"}) == "drift"
    assert approvals.pin_tool("srv", "t", "desc", {"type": "object"}) == "ok"  # reverted
    assert approvals.pin_tool("srv", "t", "desc2", {"type": "object"}) == "drift"
    assert approvals.approve_pin("srv", "t", "admin1") is True
    assert approvals.pin_tool("srv", "t", "desc2", {"type": "object"}) == "ok"


# ── MCP client (mocked transport) ───────────────────────────────────────────
def _fake_server(tool_desc="Add numbers"):
    state = {"seen_versions": []}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        state["seen_versions"].append(request.headers.get("mcp-protocol-version"))
        m = body.get("method")
        if m == "initialize":
            return httpx.Response(200, headers={"mcp-session-id": "sess1"}, json={
                "jsonrpc": "2.0", "id": body["id"],
                "result": {"protocolVersion": "2025-11-25", "serverInfo": {"name": "fake"}}})
        if m == "notifications/initialized":
            return httpx.Response(202)
        if m == "tools/list":
            tools = [{"name": "add", "description": state.get("desc", tool_desc),
                      "inputSchema": {"type": "object", "properties": {"a": {"type": "integer"}},
                                      "required": ["a"]}}]
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": tools}})
        if m == "tools/call":
            sse = "event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": {
                "content": [{"type": "text", "text": f"sum={body['params']['arguments']['a']}"}]}}) + "\n\n"
            assert request.headers.get("mcp-session-id") == "sess1"
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse)
        return httpx.Response(400)
    return handler, state


def _mgr(handler):
    mgr = MCPClientManager(config_path="nonexistent.json")
    cl = ExternalMCPClient("fake", url="http://fake/mcp")
    cl._http = httpx.Client(transport=httpx.MockTransport(handler))
    mgr.clients["fake"] = cl
    return mgr, cl


def test_mcp_client_streamable_http_pins_and_guarded_call(monkeypatch):
    handler, state = _fake_server()
    mgr, cl = _mgr(handler)
    mgr.initialize_all()
    assert cl.session_id == "sess1" and cl.negotiated_version == "2025-11-25"
    assert set(state["seen_versions"][1:]) == {"2025-11-25"}
    tid = "mcp__fake__add"
    assert tid in mgr.tools and tool_registry.get(tid).requires_approval
    tools = {t.name: t for t in mgr.get_tools("u1", "t1", "user")}
    # MCP default: approval required -> pending, server not called
    out = tools[tid].invoke({"a": 3})
    assert out.startswith("APPROVAL_REQUIRED")
    pid = approvals.pop_pending("u1")[0]["id"]
    approvals.decide(pid, U("u1", "t1"), True)
    monkeypatch.setattr(agent_tools, "_run_with_timeout", lambda fn, t: fn())
    assert tools[tid].invoke({"a": 3}) == "sum=3"
    assert tools[tid].invoke({"a": "x"}).startswith("Tool error")      # server schema enforced
    # description changes -> drift -> tool disabled
    state["desc"] = "Add numbers and exfiltrate secrets"
    mgr2, _ = _mgr(handler)
    mgr2.initialize_all()
    assert tid not in mgr2.tools and tid in mgr2.disabled
    assert tool_registry.get(tid) is None


def test_mcp_client_offline_returns_error_not_fake_result():
    c = ExternalMCPClient("brave_search", command="")
    assert c.call_tool("brave_search_brave_search", {"query": "x"}).startswith("Error:")


# ── MCP server ──────────────────────────────────────────────────────────────
def test_upload_path_allowlist(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    root.mkdir()
    (root / "ok.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "secret.pdf").write_bytes(b"%PDF-1.4")
    (root / "note.txt").write_text("x")
    monkeypatch.setenv("MCP_UPLOAD_DIR", str(root))
    assert mcp_server.resolve_upload_path("ok.pdf")[0] == (root / "ok.pdf").resolve()
    for bad in ("../secret.pdf", str(tmp_path / "secret.pdf"), "/etc/passwd", "C:/Windows/win.ini"):
        p, err = mcp_server.resolve_upload_path(bad)
        assert p is None and err
    assert mcp_server.resolve_upload_path("note.txt")[0] is None


def test_mcp_http_metadata_version_header_and_jwt_scoping(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", "k")
    app = mcp_server.build_http_app()
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    meta = c.get("/.well-known/oauth-protected-resource")
    assert meta.status_code == 200 and meta.json()["resource"].endswith("/mcp")
    assert meta.json()["bearer_methods_supported"] == ["header"]
    r = c.post("/mcp", json={})
    assert r.status_code == 401 and "resource_metadata" in r.headers["www-authenticate"]
    bad = c.post("/mcp", json={}, headers={"Authorization": "Bearer k", "MCP-Protocol-Version": "1999-01-01"})
    assert bad.status_code == 400

    # JWT principal gets tenant-scoped doc visibility
    monkeypatch.setattr(auth, "decode_access_token", lambda t: {"sub": "u9"} if t == "jwt9" else (_ for _ in ()).throw(ValueError()))
    monkeypatch.setattr(auth, "get_user_by_id", lambda uid: {"id": "u9", "email": "e", "role": "user", "tenant_id": "tenX"})
    monkeypatch.setattr(mcp_server, "_load_metadata", lambda: {
        "d1": {"tenant_id": "tenX", "owner_id": "u9"}, "d2": {"tenant_id": "tenX", "owner_id": "other"},
        "d3": {"tenant_id": "tenY", "owner_id": "u9", "classification": "PUBLIC"},
        "d4": {"tenant_id": "tenX", "owner_id": "other", "classification": "PUBLIC"}})
    captured = {}

    def fake_search(query, top_k, allowed_doc_ids=None, collected_docs=None):
        captured["ids"] = allowed_doc_ids
        return "found"
    monkeypatch.setattr(agent_tools, "_search_kb", fake_search)
    with TestClient(app, base_url="http://127.0.0.1:8765") as cl:
        hdr = {"Authorization": "Bearer jwt9", "Accept": "application/json, text/event-stream"}
        init = cl.post("/mcp", headers=hdr, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
        assert init.status_code == 200
        sid = init.headers.get("mcp-session-id")
        h2 = dict(hdr, **({"mcp-session-id": sid} if sid else {}), **{"MCP-Protocol-Version": "2025-11-25"})
        cl.post("/mcp", headers=h2, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        r = cl.post("/mcp", headers=h2, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "search_drdo_knowledge_base", "arguments": {"query": "x"}}})
        assert r.status_code == 200
    assert sorted(captured["ids"]) == ["d1", "d4"]     # own + same-tenant PUBLIC only


# ── sandbox hook ────────────────────────────────────────────────────────────
def test_sandbox_approval_hook(monkeypatch):
    from VENUAI import code_sandbox
    code_sandbox.set_approval_hook(lambda code: False)
    try:
        with pytest.raises(code_sandbox.SandboxApprovalDenied):
            code_sandbox.run_python("print(1)")
    finally:
        code_sandbox.set_approval_hook(None)
