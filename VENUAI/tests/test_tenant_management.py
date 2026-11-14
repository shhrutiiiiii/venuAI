"""
Real end-to-end tests for Phase 4 tenant management: renaming a tenant,
adjusting its quotas, adding members directly (no email/invite system
exists — see auth.py's add_tenant_member docstring), listing members, and
enforcing document/dataset/user quotas. See Documents/Backend/multi-tenancy.md.
"""

import io

import pandas as pd
import pytest

from VENUAI import rag_server


def _real_csv_bytes() -> bytes:
    df = pd.DataFrame({"category": ["a", "b"], "revenue": [10.0, 20.0]})
    return df.to_csv(index=False).encode()


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


def test_tenant_usage_reports_real_counts(client):
    _, headers = _register_and_login(client, "owner@usage.com", "Str0ng-Test-Pw-123", organization_name="Usage Co")

    r = client.get("/tenants/usage", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["user_count"] == 1
    assert body["document_count"] == 0
    assert body["dataset_count"] == 0
    assert body["max_users"] == 20


def test_non_admin_cannot_update_tenant_settings(client):
    _, headers = _register_and_login(client, "member@rename.com", "Str0ng-Test-Pw-123", organization_name="Rename Co")
    # The org creator is the tenant's first member but gets role "user" by
    # default (create_user's role param isn't set from organization_name) —
    # only the seeded demo admin or an explicitly admin-created account has
    # the admin role, so this request must be rejected.
    r = client.patch("/tenants", json={"name": "New Name"}, headers=headers)
    assert r.status_code == 403


def test_admin_can_rename_and_requota_tenant(client):
    """Promote via direct DB-less path isn't available from the API, so
    this test uses the seeded demo admin account (tenant 'default') —
    renaming/requoting the admin's own tenant."""
    login = client.post("/auth/login", json={"email": "admin@chat.com", "password": "admin12345"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    r = client.patch("/tenants", json={"name": "DRDO Central", "max_documents": 5}, headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "DRDO Central"
    assert body["max_documents"] == 5

    tenant = client.get("/auth/tenant", headers=headers)
    assert tenant.json()["name"] == "DRDO Central"
    assert tenant.json()["max_documents"] == 5


def test_admin_can_add_and_list_tenant_members(client):
    login = client.post("/auth/login", json={"email": "admin@chat.com", "password": "admin12345"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    r = client.post("/tenants/members", json={"email": "newmember@chat.com", "password": "Str0ng-Test-Pw-123"}, headers=headers)
    assert r.status_code == 201, r.text
    assert r.json()["tenant_id"] == "default"

    members = client.get("/tenants/members", headers=headers)
    assert members.status_code == 200
    emails = {m["email"] for m in members.json()}
    assert "newmember@chat.com" in emails
    assert "demo@chat.com" in emails


def test_non_admin_cannot_add_tenant_members(client):
    _, headers = _register_and_login(client, "notadmin@members.com", "Str0ng-Test-Pw-123")
    r = client.post("/tenants/members", json={"email": "sneaky@test.com", "password": "Str0ng-Test-Pw-123"}, headers=headers)
    assert r.status_code == 403


def test_adding_member_beyond_quota_is_rejected(client):
    login = client.post("/auth/login", json={"email": "admin@chat.com", "password": "admin12345"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # Shrink the quota to exactly current membership (demo + admin = 2).
    client.patch("/tenants", json={"max_users": 2}, headers=headers)

    r = client.post("/tenants/members", json={"email": "overflow@chat.com", "password": "Str0ng-Test-Pw-123"}, headers=headers)
    assert r.status_code == 403
    assert "limit" in r.json()["detail"].lower()


def test_document_quota_blocks_further_uploads(client, monkeypatch):
    """Upload isn't exercised here (needs a real PDF + embedding model —
    see test_upload_graph_autopopulation.py) — this asserts the quota gate
    itself fires correctly against the real tenant-scoped document count."""
    _, headers = _register_and_login(client, "quota@docs.com", "Str0ng-Test-Pw-123", organization_name="Doc Quota Co")

    tenant = client.get("/auth/tenant", headers=headers).json()
    login = client.post("/auth/login", json={"email": "quota@docs.com", "password": "Str0ng-Test-Pw-123"})
    # Promote isn't exposed via API; use the tenant's own PATCH as a non-admin
    # user is rejected, so directly shrink quota via the auth module for
    # this test's purposes (the org creator isn't auto-admin — see note above).
    from VENUAI import auth
    auth.update_tenant(tenant["tenant_id"], max_documents=0)

    r = client.post(
        "/upload", files={"file": ("x.pdf", b"%PDF-1.4 fake", "application/pdf")}, headers=headers,
    )
    assert r.status_code == 403
    assert "document limit" in r.json()["detail"].lower()


def test_dataset_quota_blocks_further_uploads(client):
    _, headers = _register_and_login(client, "quota@datasets.com", "Str0ng-Test-Pw-123", organization_name="Dataset Quota Co")
    tenant = client.get("/auth/tenant", headers=headers).json()

    from VENUAI import auth
    auth.update_tenant(tenant["tenant_id"], max_datasets=0)

    r = client.post(
        "/datasets/upload", files={"file": ("sales.csv", _real_csv_bytes(), "text/csv")}, headers=headers,
    )
    assert r.status_code == 403
    assert "dataset limit" in r.json()["detail"].lower()


def test_registering_into_a_full_tenant_is_rejected(client):
    _, owner_headers = _register_and_login(client, "full@org.com", "Str0ng-Test-Pw-123", organization_name="Full Org")
    tenant = client.get("/auth/tenant", headers=owner_headers).json()

    from VENUAI import auth
    auth.update_tenant(tenant["tenant_id"], max_users=1)  # already has 1 member (the creator)

    invite = auth.create_invite(tenant["tenant_id"])
    r = client.post("/auth/register", json={
        "email": "second@fullorg.com", "password": "Str0ng-Test-Pw-123", "invite_token": invite,
    })
    assert r.status_code == 403
    assert "limit" in r.json()["detail"].lower()
