"""
Real tests for VENUAI/mcp_server.py — Phase 5's MCP HTTP transport +
bearer-token auth. Also a regression test for a real, previously-existing
bug: the module imported `from mcp.server.fastmcp import FastMCP`, an API
that doesn't exist in the installed mcp>=2.0 SDK (FastMCP was renamed to
MCPServer) — so this module failed to import at all before the fix.
"""

import os

import pytest
from starlette.testclient import TestClient

import VENUAI.mcp_server as mcp_server


def test_module_imports_with_the_installed_mcp_sdk():
    """Regression test: `from mcp.server.fastmcp import FastMCP` raised
    ModuleNotFoundError against the installed mcp 2.x SDK. The fact this
    test file even collects without an ImportError is itself the proof —
    this assertion just makes the intent explicit."""
    assert mcp_server.mcp is not None
    assert mcp_server.mcp.name == "VenuAI DRDO Knowledge Base"


def test_build_http_app_refuses_without_api_key(monkeypatch):
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="MCP_API_KEY"):
        mcp_server.build_http_app()


def test_http_transport_rejects_missing_bearer_token(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", "test-secret-key")
    app = mcp_server.build_http_app()
    client = TestClient(app)

    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r.status_code == 401


def test_http_transport_rejects_wrong_bearer_token(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", "test-secret-key")
    app = mcp_server.build_http_app()
    client = TestClient(app)

    r = client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert r.status_code == 401


def test_http_transport_accepts_correct_bearer_token_and_speaks_mcp(monkeypatch):
    """The MCP session manager needs its lifespan startup/shutdown events
    to run (it initializes an internal task group there) — only
    `TestClient` used as a context manager triggers ASGI lifespan events;
    a bare `TestClient(app).post(...)` does not, which is exactly what
    the two rejection tests above don't hit (the 401 response is returned
    by this module's own middleware, before the request ever reaches the
    MCP session manager that needs the lifespan to have run)."""
    monkeypatch.setenv("MCP_API_KEY", "test-secret-key")
    app = mcp_server.build_http_app()

    # base_url matters here: the mcp SDK auto-enables DNS-rebinding
    # protection for a "127.0.0.1" host with allowed_hosts=["127.0.0.1:*",
    # ...] — a real security feature, not something to loosen for the
    # test. The ":*" pattern requires an explicit port in the Host header
    # (see mcp.server.transport_security._validate_host), which httpx
    # omits for a bare "http://127.0.0.1" base_url (default port 80) —
    # so a port must be included, any port, to match what a genuine
    # client connecting to this server's actual bind address would send.
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        r = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}},
            },
            headers={
                "Authorization": "Bearer test-secret-key",
                "Accept": "application/json, text/event-stream",
            },
        )
    assert r.status_code == 200
    assert "VenuAI DRDO Knowledge Base" in r.text
