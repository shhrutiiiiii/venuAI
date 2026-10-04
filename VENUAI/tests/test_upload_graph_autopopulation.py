"""
Real end-to-end TestClient test proving POST /upload automatically
populates the knowledge graph (Phase 4) — deterministic extraction only,
no LLM call, so every upload stays fast and free. Uses a genuinely
generated PDF (via PyMuPDF) uploaded through the actual HTTP layer, and the
real SqliteGraphStore (isolated to a temp file by the autouse
_isolated_graph_store_db fixture in conftest.py).
"""

import io

import fitz
import pytest

from VENUAI import rag_server


def _build_pdf_mentioning_lab_and_weapon() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((50, 50), "LRDE has developed the Akash missile guidance radar.", fontsize=12)
    buf = io.BytesIO(doc.write())
    doc.close()
    return buf.getvalue()


class _FakeVectorStore:
    """Stands in for the real Chroma store — /upload just needs
    .add_documents()/.persist() to exist; the graph auto-population path
    under test doesn't touch the vector store at all."""

    def add_documents(self, docs):
        pass

    def persist(self):
        pass


@pytest.fixture(autouse=True)
def _isolated_upload_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(rag_server, "META_FILE", tmp_path / "doc_metadata.json")
    upload_dir = tmp_path / "uploaded_docs"
    upload_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(rag_server, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: _FakeVectorStore())


@pytest.fixture()
def auth_headers(client):
    login = client.post("/auth/login", json={"email": "demo@chat.com", "password": "demo12345"})
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_upload_automatically_populates_knowledge_graph(client, auth_headers):
    pdf_bytes = _build_pdf_mentioning_lab_and_weapon()
    upload = client.post(
        "/upload", files={"file": ("report.pdf", pdf_bytes, "application/pdf")}, headers=auth_headers,
    )
    assert upload.status_code == 200, upload.text

    search = client.get("/graph/search?q=LRDE", headers=auth_headers)
    assert search.status_code == 200
    names = [e["name"] for e in search.json()]
    assert "LRDE" in names

    search_weapon = client.get("/graph/search?q=Akash", headers=auth_headers)
    assert "Akash" in [e["name"] for e in search_weapon.json()]


def test_upload_still_succeeds_if_graph_population_fails(client, auth_headers, monkeypatch):
    """Graph auto-population is best-effort — a failure there must never
    fail the document upload itself."""
    from VENUAI.knowledge_graph import store as kg_store_module

    def _broken_get_graph_store():
        raise RuntimeError("simulated graph store outage")

    monkeypatch.setattr(kg_store_module, "get_graph_store", _broken_get_graph_store)

    pdf_bytes = _build_pdf_mentioning_lab_and_weapon()
    upload = client.post(
        "/upload", files={"file": ("report.pdf", pdf_bytes, "application/pdf")}, headers=auth_headers,
    )
    assert upload.status_code == 200, upload.text
