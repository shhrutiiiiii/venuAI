"""
Real, end-to-end tests of authentication and authorization — run against an
actual FastAPI TestClient hitting the actual routes in rag_server.py, with a
throwaway SQLite database. No mocking of the auth logic itself.
"""


def test_protected_route_rejects_anonymous(client):
    r = client.get("/documents")
    assert r.status_code == 401


def test_admin_route_rejects_anonymous(client):
    r = client.get("/analytics/dashboard")
    assert r.status_code == 401


def test_login_with_seeded_demo_user_succeeds(client):
    r = client.post("/auth/login", json={"email": "demo@chat.com", "password": "demo12345"})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["email"] == "demo@chat.com"
    assert body["user"]["role"] == "user"
    assert body["access_token"]
    assert body["refresh_token"]


def test_login_with_wrong_password_fails(client):
    r = client.post("/auth/login", json={"email": "demo@chat.com", "password": "wrong-password"})
    assert r.status_code == 401


def test_login_with_unknown_email_fails(client):
    r = client.post("/auth/login", json={"email": "nobody@nowhere.com", "password": "whatever123"})
    assert r.status_code == 401


def test_authenticated_user_can_access_documents(client):
    login = client.post("/auth/login", json={"email": "demo@chat.com", "password": "demo12345"})
    token = login.json()["access_token"]
    r = client.get("/documents", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json() == []


def test_regular_user_cannot_access_admin_analytics(client):
    login = client.post("/auth/login", json={"email": "demo@chat.com", "password": "demo12345"})
    token = login.json()["access_token"]
    r = client.get("/analytics/dashboard", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_admin_can_access_admin_analytics(client):
    login = client.post("/auth/login", json={"email": "admin@chat.com", "password": "admin12345"})
    token = login.json()["access_token"]
    r = client.get("/analytics/dashboard", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_me_endpoint_returns_current_user(client):
    login = client.post("/auth/login", json={"email": "demo@chat.com", "password": "demo12345"})
    token = login.json()["access_token"]
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "demo@chat.com"


def test_refresh_token_rotation_invalidates_old_token(client):
    login = client.post("/auth/login", json={"email": "demo@chat.com", "password": "demo12345"})
    refresh = login.json()["refresh_token"]

    r1 = client.post("/auth/refresh", json={"refresh_token": refresh})
    assert r1.status_code == 200

    # the old refresh token must now be rejected — reuse is a replay attack
    r2 = client.post("/auth/refresh", json={"refresh_token": refresh})
    assert r2.status_code == 401


def test_register_then_login_new_user(client):
    r = client.post("/auth/register", json={"email": "newuser@test.com", "password": "supersecret123"})
    assert r.status_code == 201
    assert r.json()["role"] == "user"

    r2 = client.post("/auth/login", json={"email": "newuser@test.com", "password": "supersecret123"})
    assert r2.status_code == 200


def test_cannot_register_duplicate_email(client):
    client.post("/auth/register", json={"email": "dup@test.com", "password": "supersecret123"})
    r = client.post("/auth/register", json={"email": "dup@test.com", "password": "anotherpass123"})
    assert r.status_code == 409


def test_document_delete_by_non_owner_is_forbidden(client, isolated_auth_db):
    """
    A document created with one owner_id must not be deletable by a
    different authenticated user — only its owner or an admin may delete it.
    """
    from VENUAI import rag_server

    doc_id = "fake-doc-id-for-test"
    original_meta = rag_server.load_metadata()  # never clobber real existing metadata
    meta = dict(original_meta)
    meta[doc_id] = {
        "id": doc_id,
        "filename": "secret.pdf",
        "pages": 1,
        "chunks": 1,
        "upload_time": "2026-01-01T00:00:00",
        "size_kb": 1.0,
        "file_path": "",
        "tags": {},
        "owner_id": "someone-elses-user-id",
        "classification": "UNKNOWN",
    }
    rag_server.save_metadata(meta)

    try:
        login = client.post("/auth/login", json={"email": "demo@chat.com", "password": "demo12345"})
        token = login.json()["access_token"]

        r = client.delete(f"/documents/{doc_id}", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 404
    finally:
        # restore exactly what was there before — never clobber real uploaded-doc metadata
        rag_server.save_metadata(original_meta)
