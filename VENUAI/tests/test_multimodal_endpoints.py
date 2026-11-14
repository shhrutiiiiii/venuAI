"""
Real end-to-end TestClient tests for the Phase 2 multimodal endpoints
(/upload/multimodal, /documents/{id}/regions, /documents/{id}/regions/{id}/ask).
Uses a genuinely generated PDF (via PyMuPDF) uploaded through the actual
HTTP layer — proves the whole chain (auth → validation → real PDF parsing →
storage → retrieval → VisionAgent) is wired together, not just unit-tested
in isolation.
"""

import io

import fitz
import pytest

from VENUAI import rag_server


def _build_sample_pdf_bytes() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=400, height=500)
    page.insert_text((50, 50), "DRDO Technical Report", fontsize=24)
    page.insert_text((50, 100), "The Agni-V missile has a range of 5000 km.", fontsize=11)

    table_top, table_left = 150, 50
    col_width, row_height = 100, 25
    for row in range(3):
        y = table_top + row * row_height
        page.draw_line((table_left, y), (table_left + col_width * 2, y))
    for col in range(3):
        x = table_left + col * col_width
        page.draw_line((x, table_top), (x, table_top + row_height * 2))
    page.insert_text((table_left + 5, table_top + 17), "Parameter")
    page.insert_text((table_left + col_width + 5, table_top + 17), "Value")
    page.insert_text((table_left + 5, table_top + row_height + 17), "Range")
    page.insert_text((table_left + col_width + 5, table_top + row_height + 17), "5000 km")

    buf = io.BytesIO(doc.write())
    doc.close()
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _isolated_doc_metadata(monkeypatch, tmp_path):
    """
    rag_server.load_metadata()/save_metadata() read/write a real file
    (VENUAI/doc_metadata.json) that is NOT reset between test runs — unlike
    the auth db, which the `isolated_auth_db` fixture gives a fresh temp
    file per test. Without this, uploading "sample.pdf" here would
    permanently write into the actual project's real document metadata
    store every time this test file runs.
    """
    monkeypatch.setattr(rag_server, "META_FILE", tmp_path / "doc_metadata.json")
    # Likewise, /upload/multimodal saves the uploaded PDF under the real
    # UPLOAD_DIR (VENUAI/uploaded_docs/) — redirect that to a temp dir too,
    # so this test file doesn't leave real PDF files behind on every run.
    upload_dir = tmp_path / "uploaded_docs"
    upload_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(rag_server, "UPLOAD_DIR", upload_dir)


@pytest.fixture()
def auth_headers(client):
    login = client.post("/auth/login", json={"email": "demo@chat.com", "password": "demo12345"})
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_upload_multimodal_requires_auth(client):
    pdf_bytes = _build_sample_pdf_bytes()
    r = client.post("/upload/multimodal", files={"file": ("sample.pdf", pdf_bytes, "application/pdf")})
    assert r.status_code == 401


def test_upload_multimodal_extracts_real_table(client, auth_headers):
    pdf_bytes = _build_sample_pdf_bytes()
    r = client.post(
        "/upload/multimodal",
        files={"file": ("sample.pdf", pdf_bytes, "application/pdf")},
        headers=auth_headers,
    )
    assert r.status_code == 200
    summary = r.json()
    assert summary["total_tables"] >= 1
    assert any("5000 km" in reg["extracted_text"] for page in summary["pages"] for reg in page["regions"])


def test_other_user_cannot_view_private_multimodal_document(client, auth_headers, monkeypatch):
    pdf_bytes = _build_sample_pdf_bytes()
    upload = client.post(
        "/upload/multimodal",
        files={"file": ("sample.pdf", pdf_bytes, "application/pdf")},
        headers=auth_headers,
    )
    # Use the id the upload response itself returns — searching
    # load_metadata() by filename is fragile, since doc_metadata.json is a
    # real on-disk file that persists across test runs (unlike the
    # per-test-isolated auth db), so repeated runs can accumulate multiple
    # "sample.pdf" entries from different demo-user ids.
    doc_id = upload.json()["document_id"]

    # Register and log in as a second, unrelated user.
    client.post("/auth/register", json={"email": "other-user@test.com", "password": "supersecret123"})
    login2 = client.post("/auth/login", json={"email": "other-user@test.com", "password": "supersecret123"})
    token2 = login2.json()["access_token"]

    r = client.get(f"/documents/{doc_id}/regions", headers={"Authorization": f"Bearer {token2}"})
    assert r.status_code == 404


def test_ask_about_table_region_returns_real_answer(client, auth_headers, monkeypatch):
    class _FakeChoice:
        def __init__(self, content):
            self.message = type("M", (), {"content": content})()

    class _FakeResponse:
        def __init__(self, content):
            self.choices = [_FakeChoice(content)]
            self.usage = None

    class _FakeGroqClient:
        def __init__(self):
            class _Completions:
                def create(inner_self, model, messages, max_tokens, temperature, **_kw):
                    return _FakeResponse("The range value is 5000 km.")

            class _Chat:
                completions = _Completions()

            self.chat = _Chat()

    monkeypatch.setattr("groq.Groq", lambda api_key=None, **_kw: _FakeGroqClient())

    pdf_bytes = _build_sample_pdf_bytes()
    upload = client.post(
        "/upload/multimodal",
        files={"file": ("sample.pdf", pdf_bytes, "application/pdf")},
        headers=auth_headers,
    )
    summary = upload.json()
    doc_id = summary["document_id"]
    table_region_id = next(
        r["region_id"] for page in summary["pages"] for r in page["regions"] if r["region_type"] == "table"
    )

    r = client.post(
        f"/documents/{doc_id}/regions/{table_region_id}/ask",
        json={"question": "What is the range value?"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["insufficient_visual_evidence"] is False
    assert "5000" in body["answer"]
