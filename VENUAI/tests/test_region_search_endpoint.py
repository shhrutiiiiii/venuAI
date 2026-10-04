"""Real TestClient test for POST /documents/regions/search."""

import io

import fitz
import pytest

from VENUAI import rag_server


def _build_sample_pdf_bytes() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    table_top, table_left = 50, 50
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
def _isolated_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(rag_server, "META_FILE", tmp_path / "doc_metadata.json")
    upload_dir = tmp_path / "uploaded_docs"
    upload_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(rag_server, "UPLOAD_DIR", upload_dir)


def test_search_requires_auth(client):
    r = client.post("/documents/regions/search", json={"query": "parameter"})
    assert r.status_code == 401


def test_search_finds_uploaded_table(client):
    login = client.post("/auth/login", json={"email": "demo@chat.com", "password": "demo12345"})
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    client.post(
        "/upload/multimodal",
        files={"file": ("doc.pdf", _build_sample_pdf_bytes(), "application/pdf")},
        headers=headers,
    )

    r = client.post("/documents/regions/search", json={"query": "Parameter"}, headers=headers)
    assert r.status_code == 200
    results = r.json()
    assert len(results) >= 1
    assert results[0]["region_type"] == "table"
