"""GET /features and POST /documents/{id}/summaries."""
import uuid

import pytest

from VENUAI import raptor, rag_server

PW = "Str0ng-Test-Pw-123"


def _login(client, email, org=None):
    body = {"email": email, "password": PW}
    if org:
        body["organization_name"] = org
    assert client.post("/auth/register", json=body).status_code == 201
    tok = client.post("/auth/login", json={"email": email, "password": PW}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_features_requires_auth(client):
    assert client.get("/features").status_code in (401, 403)


def test_features_shape_and_env(client, monkeypatch):
    h = _login(client, "f1a@example.com", "F1 Org A")
    monkeypatch.delenv("LONG_TERM_MEMORY", raising=False)
    monkeypatch.delenv("VOICE_ENABLED", raising=False)
    monkeypatch.delenv("RAPTOR", raising=False)
    feats = {f["key"]: f for f in client.get("/features", headers=h).json()["features"]}
    for k in ("long_term_memory", "voice", "raptor", "contextual_chunking", "safeguard", "local_faithfulness"):
        f = feats[k]
        assert {"label", "enabled", "per_user_opt_in", "token_cost", "token_cost_note", "enable_with", "requires"} <= set(f)
        assert f["token_cost"] in ("none", "low", "medium", "high")
    assert feats["long_term_memory"]["per_user_opt_in"] is True
    assert not feats["long_term_memory"]["enabled"] and not feats["voice"]["enabled"] and not feats["raptor"]["enabled"]
    monkeypatch.setenv("LONG_TERM_MEMORY", "on")
    monkeypatch.setenv("VOICE_ENABLED", "on")
    monkeypatch.setenv("RAPTOR", "on")
    monkeypatch.setenv("CONTEXTUAL_CHUNKING", "false")
    feats = {f["key"]: f for f in client.get("/features", headers=h).json()["features"]}
    assert feats["long_term_memory"]["enabled"] and feats["voice"]["enabled"] and feats["raptor"]["enabled"]
    assert feats["contextual_chunking"]["enabled"] is False


def _doc(user_tenant, owner_id):
    did = str(uuid.uuid4())
    meta = rag_server.load_metadata()
    meta[did] = {"id": did, "filename": "x.pdf", "pages": 1, "chunks": 0, "upload_time": "t", "size_kb": 1.0,
                 "file_path": "", "tags": {}, "owner_id": owner_id, "tenant_id": user_tenant, "classification": "UNKNOWN"}
    rag_server.save_metadata(meta)
    return did


def test_raptor_endpoint(client, monkeypatch):
    ha = _login(client, "f1b@example.com", "F1 Org B")
    hc = _login(client, "f1c@example.com", "F1 Org C")
    me = client.get("/auth/me", headers=ha).json()
    other = client.get("/auth/me", headers=hc).json()
    tid = me.get("tenant_id") or me.get("organization_id")
    did = _doc(tid, me["id"])
    # another tenant cannot touch it
    assert client.post(f"/documents/{did}/summaries", headers=hc).status_code in (403, 404)
    assert client.post(f"/documents/{uuid.uuid4()}/summaries", headers=ha).status_code == 404
    # off -> 503
    monkeypatch.delenv("RAPTOR", raising=False)
    r = client.post(f"/documents/{did}/summaries", headers=ha)
    assert r.status_code == 503 and r.json()["detail"] == "RAPTOR disabled"
    # on -> mocked build
    monkeypatch.setenv("RAPTOR", "on")

    class VS:
        def get(self, where=None):
            return {"documents": ["a", "b", "c", "d"]}

    calls = []
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: VS())
    monkeypatch.setattr(raptor, "build_summaries", lambda d, t, c: calls.append((d, t, len(c))) or 3)
    r = client.post(f"/documents/{did}/summaries", headers=ha)
    assert r.status_code == 200 and r.json()["summaries_stored"] == 3
    assert calls == [(did, tid, 4)]
    assert other  # silence unused
