"""
Shared pytest fixtures. Sets required environment variables *before* any
VENUAI module is imported (several modules raise at import time if their
required env var is missing — fail loud, not silently permissive), and
isolates the auth database to a temp file so tests never touch real user data.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

os.environ.setdefault("GROQ_API_KEY", "test-key-not-real")
os.environ.setdefault("GROQ_MODEL", "openai/gpt-oss-120b")
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-7f3a9c2e1b8d4f6a-not-for-prod")
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:3000")


@pytest.fixture(autouse=True)
def _reset_ai_gateway_singleton():
    """
    AIGateway's module-level singleton caches whichever groq_client is first
    passed to get_gateway(). That's correct in production (one real client,
    constructed once) but would silently leak a fake client from one test
    into the next test's assertions without this reset. Autouse so every
    test file gets a clean gateway, not just ones that remember to ask for it.
    """
    from VENUAI.ai_gateway.gateway import reset_gateway_singleton
    from VENUAI.verifier_agent import reset_verifier_singleton
    reset_gateway_singleton()
    reset_verifier_singleton()
    yield
    reset_gateway_singleton()
    reset_verifier_singleton()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """
    slowapi's Limiter keeps its hit counters in process-global in-memory
    storage, keyed by client IP — which TestClient always reports as the
    same fake address. Across the full test suite, many tests call
    /auth/login etc., and without resetting this between tests the real
    "10 per minute" login limit gets exhausted by *earlier* tests, making
    *later* tests fail with 429s that have nothing to do with what they're
    actually testing. Production correctly keeps this state across real
    requests — only the test suite needs isolation here.
    """
    from VENUAI.rag_server import limiter
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture(autouse=True)
def _isolated_guardrail_audit_db(monkeypatch, tmp_path):
    """
    GuardrailEngine.log_violation() writes to a real file
    (VENUAI/data/guardrails_audit.db) that is NOT reset between test runs —
    same lesson as doc_metadata.json (see test_multimodal_endpoints.py).
    Any test that triggers a guardrail violation (red-team prompts, the
    Security Center tests) would otherwise permanently write into the
    actual project's audit log on every test run. Autouse so this applies
    even to test files that don't know they're exercising guardrails
    indirectly (e.g. /query with an injection-shaped question).
    """
    from VENUAI import guardrails
    monkeypatch.setattr(guardrails, "DB_PATH", tmp_path / "guardrails_audit.db")
    guardrails.reset_guardrail_engine_singleton()
    yield
    guardrails.reset_guardrail_engine_singleton()


@pytest.fixture(autouse=True)
def _isolated_graph_store_db(monkeypatch, tmp_path):
    """
    SqliteGraphStore defaults to a real file (VENUAI/data/knowledge_graph.db)
    when get_graph_store() is called with no prior isolation — same lesson
    as doc_metadata.json and guardrails_audit.db. Previously only
    test_graph_endpoints.py isolated this (file-local fixture); now that
    POST /upload also populates the graph automatically (Phase 4), any test
    that exercises /upload needs the same isolation, so this is autouse at
    the suite level instead.
    """
    from VENUAI.knowledge_graph import store as kg_store_module

    kg_store_module.reset_graph_store_singleton()
    isolated_store = kg_store_module.SqliteGraphStore(db_path=tmp_path / "knowledge_graph_test.db")
    monkeypatch.setattr(kg_store_module, "get_graph_store", lambda: isolated_store)
    yield
    kg_store_module.reset_graph_store_singleton()


@pytest.fixture(autouse=True)
def _isolated_evidence_store_db(monkeypatch, tmp_path):
    """
    VENUAI.evidence_store writes to a real file
    (VENUAI/data/evidence_explorer.db) by default — same lesson as
    doc_metadata.json/guardrails_audit.db/knowledge_graph.db. POST /query
    and POST /research both persist an evidence record via a
    BackgroundTask, which FastAPI's TestClient runs synchronously, so any
    test hitting either route would otherwise write into the real project
    file on every run.
    """
    from VENUAI import evidence_store

    monkeypatch.setattr(evidence_store, "DB_PATH", tmp_path / "evidence_explorer_test.db")
    evidence_store.init_db()
    yield


@pytest.fixture()
def isolated_auth_db(monkeypatch, tmp_path):
    """Point the auth module at a throwaway SQLite file for this test only."""
    from VENUAI import auth

    db_path = tmp_path / "users.db"
    monkeypatch.setattr(auth, "DB_PATH", db_path)
    auth.init_db()
    auth.seed_demo_users()
    yield auth


@pytest.fixture()
def client(isolated_auth_db):
    from fastapi.testclient import TestClient
    from VENUAI import rag_server

    return TestClient(rag_server.app)
