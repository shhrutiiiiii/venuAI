"""
Real end-to-end tests for Phase 4 multi-tenancy — see
Documents/Backend/multi-tenancy.md. Registration, the /auth/tenant
endpoint, and cross-tenant data isolation for datasets and multimodal
documents, all driven through the actual HTTP layer (TestClient), not
mocked access-control functions.
"""

import io

import fitz
import pandas as pd
import pytest

from VENUAI import rag_server


def _real_csv_bytes() -> bytes:
    df = pd.DataFrame({"category": ["a", "b"], "revenue": [10.0, 20.0]})
    return df.to_csv(index=False).encode()


def _build_sample_pdf_bytes() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=300, height=300)
    page.insert_text((50, 50), "A tenant-isolated document.", fontsize=14)
    buf = io.BytesIO(doc.write())
    doc.close()
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _isolated_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(rag_server, "META_FILE", tmp_path / "doc_metadata.json")
    upload_dir = tmp_path / "uploaded_docs"
    upload_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(rag_server, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(rag_server, "DATASET_META_FILE", tmp_path / "datasets.json")
    dataset_dir = tmp_path / "uploaded_datasets"
    dataset_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(rag_server, "DATASET_DIR", dataset_dir)


def _register_and_login(client, email, password, organization_name=None, tenant_id=None):
    body = {"email": email, "password": password}
    if organization_name:
        body["organization_name"] = organization_name
    if tenant_id:
        body["tenant_id"] = tenant_id
    reg = client.post("/auth/register", json=body)
    assert reg.status_code == 201, reg.text
    login = client.post("/auth/login", json={"email": email, "password": password})
    token = login.json()["access_token"]
    return reg.json(), {"Authorization": f"Bearer {token}"}


def test_registering_with_organization_name_creates_a_new_tenant(client):
    user, _ = _register_and_login(client, "founder@neworg.com", "password123", organization_name="New Org Inc")
    assert user["tenant_id"] != "default"


def test_registering_without_organization_name_joins_default_tenant(client):
    user, _ = _register_and_login(client, "nobody@test.com", "password123")
    assert user["tenant_id"] == "default"


def test_registering_with_unknown_tenant_id_fails(client):
    r = client.post("/auth/register", json={
        "email": "x@test.com", "password": "password123", "tenant_id": "does-not-exist",
    })
    assert r.status_code == 404


def test_auth_tenant_endpoint_returns_own_tenant(client):
    user, headers = _register_and_login(client, "owner@acme.com", "password123", organization_name="Acme Corp")
    r = client.get("/auth/tenant", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == user["tenant_id"]
    assert body["name"] == "Acme Corp"


def test_second_user_joining_existing_tenant_shares_it(client):
    user1, _ = _register_and_login(client, "owner2@acme.com", "password123", organization_name="Acme Corp 2")
    user2, _ = _register_and_login(client, "teammate@acme.com", "password123", tenant_id=user1["tenant_id"])
    assert user2["tenant_id"] == user1["tenant_id"]


def test_dataset_invisible_across_tenants(client):
    _, headers_a = _register_and_login(client, "a@tenant-a.com", "password123", organization_name="Tenant A")
    _, headers_b = _register_and_login(client, "b@tenant-b.com", "password123", organization_name="Tenant B")

    upload = client.post(
        "/datasets/upload", files={"file": ("sales.csv", _real_csv_bytes(), "text/csv")}, headers=headers_a,
    )
    dataset_id = upload.json()["id"]

    # Owner's own tenant sees it.
    list_a = client.get("/datasets", headers=headers_a)
    assert any(d["id"] == dataset_id for d in list_a.json())

    # A different tenant cannot see or reach it, even by id.
    list_b = client.get("/datasets", headers=headers_b)
    assert not any(d["id"] == dataset_id for d in list_b.json())
    analyze_b = client.post(f"/datasets/{dataset_id}/analyze", json={"question": "mean revenue"}, headers=headers_b)
    assert analyze_b.status_code == 404


def test_multimodal_document_invisible_across_tenants(client):
    _, headers_a = _register_and_login(client, "a2@tenant-a.com", "password123", organization_name="Tenant A2")
    _, headers_b = _register_and_login(client, "b2@tenant-b.com", "password123", organization_name="Tenant B2")

    upload = client.post(
        "/upload/multimodal",
        files={"file": ("doc.pdf", _build_sample_pdf_bytes(), "application/pdf")},
        headers=headers_a,
    )
    doc_id = upload.json()["document_id"]

    r = client.get(f"/documents/{doc_id}/regions", headers=headers_b)
    assert r.status_code == 404

    own = client.get(f"/documents/{doc_id}/regions", headers=headers_a)
    assert own.status_code == 200


def test_admin_does_not_see_other_tenants_documents(client, isolated_auth_db):
    """
    Admin is a per-tenant role, not a cross-tenant superuser (Phase 4 —
    see Documents/Backend/multi-tenancy.md) — the seeded demo admin
    (tenant "default") must not see a document uploaded inside a
    different, newly-created tenant.
    """
    _, headers_other = _register_and_login(client, "c@tenant-c.com", "password123", organization_name="Tenant C")
    upload = client.post(
        "/upload/multimodal",
        files={"file": ("doc.pdf", _build_sample_pdf_bytes(), "application/pdf")},
        headers=headers_other,
    )
    doc_id = upload.json()["document_id"]

    admin_login = client.post("/auth/login", json={"email": "admin@chat.com", "password": "admin12345"})
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}

    r = client.get(f"/documents/{doc_id}/regions", headers=admin_headers)
    assert r.status_code == 404
