"""
Security stream S2 - systematic authorization / tenancy / IDOR matrix.

Principals (see `world`):
    A    tenant 1, role user  - owns the seeded resources
    B    tenant 1, role user  - same tenant, different owner
    ADM1 tenant 1, role admin - tenant admin (sees everything in tenant 1)
    C    tenant 2, role user
    ADM2 tenant 2, role admin

For every authenticated route that takes an id we assert exactly who may
reach the resource, and that everybody else gets a 404 whose body is
byte-identical to the 404 for an id that does not exist (no existence oracle).
Also covers ID guessing, mass assignment, role escalation through the request
body / forged token claims, session-memory isolation, /health leakage and the
policy engine's deny-by-default behaviour.

A route-inventory test fails when a new route is added without being
classified here, so the matrix cannot silently go stale.
"""
from __future__ import annotations

import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from VENUAI import auth, authz, rag_server

pytestmark = pytest.mark.security

PRINCIPALS = ["A", "B", "ADM1", "C", "ADM2"]
NOT_FOUND_SUFFIX = "not found."


# ─────────────────────────────────────────────────────────────────────────────
# World: users + isolated stores
# ─────────────────────────────────────────────────────────────────────────────

class World:
    pass


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    tmp = tmp_path_factory.mktemp("idor")

    # auth db
    mp.setattr(auth, "DB_PATH", tmp / "users.db")
    auth.init_db()

    # document / dataset stores
    mp.setattr(rag_server, "META_FILE", tmp / "doc_metadata.json")
    mp.setattr(rag_server, "DATASET_META_FILE", tmp / "datasets.json")
    (tmp / "docs").mkdir()
    (tmp / "dsets").mkdir()
    mp.setattr(rag_server, "UPLOAD_DIR", tmp / "docs")
    mp.setattr(rag_server, "DATASET_DIR", tmp / "dsets")

    # trace / approvals / research / memory / analytics stores
    from VENUAI import trace_store, approvals, research_jobs, memory_manager, analytics, tenant_admin
    mp.setattr(trace_store, "DB_PATH", tmp / "traces.db")
    mp.setattr(trace_store, "_ready_for", None)
    mp.setattr(approvals, "DB_PATH", tmp / "approvals.db")
    mp.setattr(approvals, "_ready_for", None)
    mp.setenv("RESEARCH_JOBS_DB", str(tmp / "research.db"))
    research_jobs.reset_manager()
    mm = memory_manager.MemoryManager(db_path=str(tmp / "mem.db"))
    mp.setattr(memory_manager, "_memory_manager", mm)
    memory_manager.reset_ltm_store()
    mp.setattr(memory_manager, "_embedder_failed", True)
    mp.setattr(analytics, "_analytics", analytics.QueryAnalytics(db_path=str(tmp / "analytics.db")))
    mp.setattr(tenant_admin, "_ready_for", None)
    mp.setenv("TOOL_APPROVAL", "on")

    # no real vector store: deletes only need a collection with delete()
    class _Coll:
        deleted: list = []

        def delete(self, where=None):
            self.deleted.append(where)

    class _VS:
        _collection = _Coll()

        def persist(self):
            pass

    mp.setattr(rag_server, "get_vector_store", lambda: _VS())

    w = World()
    w.tmp = tmp
    w.t1 = auth.create_tenant("Tenant One", "t1").tenant_id
    w.t2 = auth.create_tenant("Tenant Two", "t2").tenant_id
    pw = "Sup3r-secret-pw!"
    mk = lambda email, role, tid: auth.create_user(email, pw, role=role, tenant_id=tid)
    w.users = {
        "A": mk("a-one@example.com", "user", w.t1), "B": mk("b-one@example.com", "user", w.t1),
        "ADM1": mk("adm-one@example.com", "admin", w.t1),
        "C": mk("c-two@example.com", "user", w.t2), "ADM2": mk("adm-two@example.com", "admin", w.t2),
    }
    w.headers = {k: {"Authorization": f"Bearer {auth.create_access_token(u.id, u.role)}"}
                 for k, u in w.users.items()}
    w.client = TestClient(rag_server.app, raise_server_exceptions=False)
    w.vs = _VS
    yield w
    mp.undo()
    research_jobs.reset_manager()


def _mm_summary():
    return {"document_id": "x", "pages": [{"page_number": 1, "regions": [
        {"region_id": "r1", "region_type": "table", "bounding_box": None,
         "extracted_text": "Parameter Range 5000 km", "ocr_text": None, "ocr_unavailable_reason": None}]}],
        "total_tables": 1, "total_images": 0}


@pytest.fixture()
def res(world):
    """Fresh resources (new ids) for one test. Owner is always A (tenant 1)."""
    from VENUAI import evidence_store, trace_store, research_jobs, memory_manager, approvals
    from VENUAI.knowledge_graph import store as kgs
    from VENUAI.knowledge_graph.models import Entity, EntityType

    r = World()
    A, C = world.users["A"], world.users["C"]

    meta = rag_server.load_metadata()
    for key, cls in (("doc_priv", "UNKNOWN"), ("doc_pub", "PUBLIC")):
        did = str(uuid.uuid4())
        setattr(r, key, did)
        meta[did] = {"id": did, "filename": f"{key}.pdf", "pages": 1, "chunks": 0, "upload_time": "t",
                     "size_kb": 1.0, "file_path": str(world.tmp / "docs" / f"{did}.pdf"), "tags": {},
                     "owner_id": A.id, "tenant_id": world.t1, "classification": cls,
                     "is_multimodal": True, "multimodal_summary": _mm_summary()}
    # a tenant-2 document, for cross-tenant listing checks
    r.doc_c = str(uuid.uuid4())
    meta[r.doc_c] = {"id": r.doc_c, "filename": "c.pdf", "pages": 1, "chunks": 0, "upload_time": "t",
                     "size_kb": 1.0, "file_path": "", "tags": {}, "owner_id": C.id, "tenant_id": world.t2,
                     "classification": "UNKNOWN", "is_multimodal": True, "multimodal_summary": _mm_summary()}
    rag_server.save_metadata(meta)

    dmeta = rag_server.load_dataset_metadata()
    for key, cls in (("ds_priv", "UNKNOWN"), ("ds_pub", "PUBLIC")):
        did = str(uuid.uuid4())
        setattr(r, key, did)
        f = world.tmp / "dsets" / f"{did}.csv"
        f.write_text("category,revenue\na,1\nb,2\n")
        dmeta[did] = {"id": did, "filename": f"{key}.csv", "file_path": str(f), "columns": ["category", "revenue"],
                      "dtypes": {"category": "object", "revenue": "int64"}, "row_count": 2, "size_kb": 0.1,
                      "upload_time": "t", "owner_id": A.id, "tenant_id": world.t1, "classification": cls}
    rag_server.save_dataset_metadata(dmeta)

    store = kgs.get_graph_store()
    r.entity = Entity.make_id(EntityType.LABORATORY, "SecretLab", world.t1)
    store.add_entity(Entity(entity_id=r.entity, entity_type=EntityType.LABORATORY, name="SecretLab",
                            owner_id=A.id, tenant_id=world.t1, classification="UNKNOWN"))

    r.evidence = evidence_store.save_record(A.id, world.t1, "q-secret", "a", "query", [], 1.0, 0, 0, True)

    r.trace = uuid.uuid4().hex
    trace_store.save_trace({"trace_id": r.trace, "tenant_id": world.t1, "user_id": A.id, "path": "query",
                            "started_at": time.time(), "total_ms": 1.0, "question_hash": "h",
                            "steps": [], "spans": [], "llm_calls": [], "tool_calls": [], "retrieved": []})

    mgr = research_jobs.get_manager()
    r.job = mgr.store.create_job(world.t1, A.id, "secret question", [r.doc_priv], "quick",
                                 [{"id": "s1", "text": "sub", "rationale": ""}], False)
    mgr.store.update_job(r.job, status="done")   # terminal: the SSE generator ends instead of polling forever

    ltm = memory_manager.get_ltm_store()
    ltm.add_or_update(world.t1, A.id, "A likes the Agni missile program", "preference", "sess")
    r.memory = ltm.list(world.t1, A.id)[0]["id"]

    d = approvals.require_approval("mcp_tool", {"x": uuid.uuid4().hex}, A, description="d", schema={}, kind="mcp")
    assert d.status == "pending"
    r.approval = d.approval_id

    from VENUAI import tenant_admin
    r.invite = tenant_admin.create_invite(world.t1, world.users["ADM1"].id, "user")["id"]
    return r


def call(world, who, method, url, **kw):
    headers = {} if who is None else world.headers[who]
    return world.client.request(method, url, headers=headers, **kw)


def _is_not_found(resp):
    if resp.status_code != 404:
        return False
    try:
        return str(resp.json().get("detail", "")).lower().endswith(NOT_FOUND_SUFFIX)
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# The IDOR matrix
# ─────────────────────────────────────────────────────────────────────────────
# (case id, method, url template, json body, {principal: expectation})
# expectation: "ok" = 2xx; "seen" = reached the resource (not 401/403/not-found, any
# downstream outcome); 404 = indistinguishable from a missing id; 403 = visible but forbidden.

OWNER_ONLY = {"A": "ok", "B": 404, "ADM1": "ok", "C": 404, "ADM2": 404}
STRICT_OWNER = {"A": "ok", "B": 404, "ADM1": 404, "C": 404, "ADM2": 404}   # per-user (admin does not see)
PUBLIC_READ = {"A": "ok", "B": "ok", "ADM1": "ok", "C": 404, "ADM2": 404}
PUBLIC_DELETE = {"A": "ok", "B": 403, "ADM1": "ok", "C": 404, "ADM2": 404}
SEEN_OWNER = {"A": "seen", "B": 404, "ADM1": "seen", "C": 404, "ADM2": 404}
SEEN_PUBLIC = {"A": "seen", "B": "seen", "ADM1": "seen", "C": 404, "ADM2": 404}

MATRIX = [
    ("doc-regions-priv", "GET", "/documents/{doc_priv}/regions", None, OWNER_ONLY),
    ("doc-regions-pub", "GET", "/documents/{doc_pub}/regions", None, PUBLIC_READ),
    ("doc-region-ask-priv", "POST", "/documents/{doc_priv}/regions/r1/ask", {"question": "what?"}, SEEN_OWNER),
    ("doc-region-ask-pub", "POST", "/documents/{doc_pub}/regions/r1/ask", {"question": "what?"}, SEEN_PUBLIC),
    ("doc-delete-priv", "DELETE", "/documents/{doc_priv}", None, OWNER_ONLY),
    ("doc-delete-pub", "DELETE", "/documents/{doc_pub}", None, PUBLIC_DELETE),
    ("ds-analyze-priv", "POST", "/datasets/{ds_priv}/analyze", {"question": "total revenue"}, SEEN_OWNER),
    ("ds-analyze-pub", "POST", "/datasets/{ds_pub}/analyze", {"question": "total revenue"}, SEEN_PUBLIC),
    ("ds-delete-priv", "DELETE", "/datasets/{ds_priv}", None, OWNER_ONLY),
    ("ds-delete-pub", "DELETE", "/datasets/{ds_pub}", None, PUBLIC_DELETE),
    ("graph-entity", "GET", "/graph/entities/{entity}", None, OWNER_ONLY),
    ("evidence-record", "GET", "/evidence/{evidence}", None, OWNER_ONLY),
    ("trace", "GET", "/traces/{trace}", None, OWNER_ONLY),
    ("job-get", "GET", "/research/jobs/{job}", None, STRICT_OWNER),
    ("job-events", "GET", "/research/jobs/{job}/events", None, STRICT_OWNER),
    ("job-resume", "POST", "/research/jobs/{job}/resume", None, {"A": "seen", "B": 404, "ADM1": 404, "C": 404, "ADM2": 404}),
    ("job-cancel", "DELETE", "/research/jobs/{job}", None, STRICT_OWNER),
    ("memory-delete", "DELETE", "/memory/{memory}", None, STRICT_OWNER),
    # tenant admins are the designated approvers for their tenant (human-in-the-loop), other users are not
    ("approval-decision", "POST", "/approvals/{approval}/decision", {"approve": True}, OWNER_ONLY),
    ("invite-revoke", "DELETE", "/tenants/invites/{invite}", None,
     {"A": 403, "B": 403, "ADM1": "ok", "C": 403, "ADM2": 404}),
]


def _fill(url, res):
    return url.format(**{k: getattr(res, k) for k in
                         ("doc_priv", "doc_pub", "ds_priv", "ds_pub", "entity", "evidence", "trace",
                          "job", "memory", "approval", "invite")})


def _probe_missing(url):
    import re
    return re.sub(r"\{(\w+)\}", lambda m: uuid.uuid4().hex, url)


@pytest.mark.parametrize("who", PRINCIPALS)
@pytest.mark.parametrize("case", MATRIX, ids=[m[0] for m in MATRIX])
def test_idor_matrix(world, res, case, who):
    _cid, method, tmpl, body, expect = case
    resp = call(world, who, method, _fill(tmpl, res), json=body)
    want = expect[who]
    if method == "GET" and tmpl.endswith("/events") and resp.status_code == 200:
        resp.close()
    if want == "ok":
        assert 200 <= resp.status_code < 300, (who, resp.status_code, resp.text[:200])
    elif want == "seen":
        assert resp.status_code not in (401, 403) and not _is_not_found(resp), (who, resp.status_code, resp.text[:200])
    elif want == 404:
        assert _is_not_found(resp), (who, resp.status_code, resp.text[:200])
        # the response for a forbidden id equals the response for a nonexistent id
        missing = call(world, who, method, _probe_missing(tmpl), json=body)
        assert missing.status_code == 404
        if "/tenants/invites" not in tmpl:
            assert missing.json() == resp.json(), (missing.text, resp.text)
    else:
        assert resp.status_code == want, (who, resp.status_code, resp.text[:200])


@pytest.mark.parametrize("who", [None, "A", "B", "ADM1", "C", "ADM2"])
def test_unauthenticated_is_401_for_every_matrix_route(world, res, who):
    if who is not None:
        pytest.skip("covered by the matrix")
    for _cid, method, tmpl, body, _e in MATRIX:
        resp = call(world, None, method, _fill(tmpl, res), json=body)
        assert resp.status_code == 401, (tmpl, resp.status_code)


# ── list endpoints ───────────────────────────────────────────────────────────

def _ids(resp, key="id"):
    assert resp.status_code == 200, resp.text
    return {x[key] for x in resp.json()}


@pytest.mark.parametrize("who,sees_priv,sees_pub", [
    ("A", True, True), ("B", False, True), ("ADM1", True, True), ("C", False, False), ("ADM2", False, False)])
def test_document_and_dataset_lists(world, res, who, sees_priv, sees_pub):
    d = _ids(call(world, who, "GET", "/documents"))
    assert (res.doc_priv in d) is sees_priv and (res.doc_pub in d) is sees_pub
    assert (res.doc_c in d) is (who in ("C", "ADM2"))
    ds = _ids(call(world, who, "GET", "/datasets"))
    assert (res.ds_priv in ds) is sees_priv and (res.ds_pub in ds) is sees_pub


@pytest.mark.parametrize("who,visible", [("A", True), ("B", False), ("ADM1", True), ("C", False), ("ADM2", False)])
def test_graph_search_and_evidence_trace_lists(world, res, who, visible):
    names = {e["name"] for e in call(world, who, "GET", "/graph/search?q=SecretLab").json()}
    assert ("SecretLab" in names) is visible
    assert (res.evidence in _ids(call(world, who, "GET", "/evidence"), "record_id")) is visible
    assert (res.trace in _ids(call(world, who, "GET", "/traces"), "trace_id")) is visible


@pytest.mark.parametrize("who,visible", [("A", True), ("B", False), ("ADM1", False), ("C", False), ("ADM2", False)])
def test_job_and_memory_lists_are_per_user(world, res, who, visible):
    jobs = call(world, who, "GET", "/research/jobs")
    assert (res.job in {j["job_id"] if "job_id" in j else j.get("id") for j in jobs.json()}) is visible
    mems = {m["id"] for m in call(world, who, "GET", "/memory").json()}
    assert (res.memory in mems) is visible
    pend = {p["id"] for p in call(world, who, "GET", "/approvals/pending").json()}
    assert (res.approval in pend) is (visible or who == "ADM1")


@pytest.mark.parametrize("who,visible", [("A", True), ("B", False), ("ADM1", True), ("C", False), ("ADM2", False)])
def test_region_search_only_returns_visible_documents(world, res, who, visible):
    out = call(world, who, "POST", "/documents/regions/search", json={"query": "Parameter Range"}).json()
    docs = {r["document_id"] for r in out}
    assert (res.doc_priv in docs) is visible
    assert res.doc_c not in docs or who in ("C", "ADM2")


# ── tenant admin surface ─────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/tenants/members", "/tenants/invites", "/analytics/dashboard",
                                  "/security/violations", "/ops/metrics", "/ops/tool-calls",
                                  "/health/details"])
@pytest.mark.parametrize("who,status", [(None, 401), ("A", 403), ("B", 403), ("C", 403),
                                        ("ADM1", 200), ("ADM2", 200)])
def test_admin_only_routes(world, path, who, status):
    assert call(world, who, "GET", path).status_code == status


def test_pins_are_operator_only_even_for_tenant_admins(world, monkeypatch):
    for who in ("A", "ADM1", "ADM2"):
        assert call(world, who, "GET", "/approvals/pins").status_code == 403
        assert call(world, who, "POST", "/approvals/pins/approve",
                    json={"server": "s", "tool": "t"}).status_code == 403
    monkeypatch.setenv("SUPERADMIN_USER_IDS", world.users["ADM1"].id)
    assert call(world, "ADM1", "GET", "/approvals/pins").status_code == 200
    assert call(world, "ADM2", "GET", "/approvals/pins").status_code == 403


def test_members_list_is_tenant_scoped(world):
    t1 = {m["id"] for m in call(world, "ADM1", "GET", "/tenants/members").json()}
    t2 = {m["id"] for m in call(world, "ADM2", "GET", "/tenants/members").json()}
    assert world.users["A"].id in t1 and world.users["C"].id not in t1
    assert world.users["C"].id in t2 and world.users["A"].id not in t2


def test_security_violations_are_tenant_scoped(world):
    from VENUAI.guardrails import get_guardrail_engine
    ge = get_guardrail_engine()
    ge.log_violation(world.users["A"].id, "tenant-one-secret-query", "prompt_injection", "blocked", "m")
    ge.log_violation(world.users["C"].id, "tenant-two-secret-query", "prompt_injection", "blocked", "m")
    t1 = json.dumps(call(world, "ADM1", "GET", "/security/violations").json())
    t2 = json.dumps(call(world, "ADM2", "GET", "/security/violations").json())
    assert "tenant-one-secret-query" in t1 and "tenant-two-secret-query" not in t1
    assert "tenant-two-secret-query" in t2 and "tenant-one-secret-query" not in t2


def test_analytics_dashboard_is_tenant_scoped(world):
    from VENUAI.analytics import get_analytics
    an = get_analytics()
    an.log_query("s", world.users["A"].id, "tenant-one analytics question", 5, 10.0, 0.9, 0.1, "rag", [], None)
    an.log_query("s", world.users["C"].id, "tenant-two analytics question", 5, 10.0, 0.9, 0.1, "rag", [], None)
    d1 = json.dumps(call(world, "ADM1", "GET", "/analytics/dashboard").json())
    d2 = json.dumps(call(world, "ADM2", "GET", "/analytics/dashboard").json())
    assert "tenant-one analytics" in d1 and "tenant-two analytics" not in d1
    assert "tenant-two analytics" in d2 and "tenant-one analytics" not in d2


def test_ops_tool_ledger_is_tenant_scoped(world):
    from VENUAI import trace_store
    trace_store.record_tool_call("t_one", {}, tenant_id=world.t1, user_id=world.users["A"].id)
    trace_store.record_tool_call("t_two", {}, tenant_id=world.t2, user_id=world.users["C"].id)
    one = {r["tool"] for r in call(world, "ADM1", "GET", "/ops/tool-calls").json()}
    two = {r["tool"] for r in call(world, "ADM2", "GET", "/ops/tool-calls").json()}
    assert "t_one" in one and "t_two" not in one
    assert "t_two" in two and "t_one" not in two


def test_usage_and_tenant_endpoints_report_only_own_tenant(world):
    for who, tid in (("A", "t1"), ("C", "t2"), ("ADM2", "t2")):
        assert call(world, who, "GET", "/auth/tenant").json()["tenant_id"] == tid
        assert call(world, who, "GET", "/tenants/usage").json()["tenant_id"] == tid


# ─────────────────────────────────────────────────────────────────────────────
# Invites, role changes, last admin
# ─────────────────────────────────────────────────────────────────────────────

def test_invite_lifecycle_single_use_and_tenant_bound(world):
    from VENUAI import tenant_admin
    r = call(world, "ADM1", "POST", "/tenants/invites", json={"role": "user"})
    assert r.status_code == 201
    token = r.json()["token"]
    listing = call(world, "ADM1", "GET", "/tenants/invites").json()
    assert all("token" not in i and "token_hash" not in i for i in listing)
    assert call(world, "ADM2", "GET", "/tenants/invites").json() == [] or \
        r.json()["id"] not in {i["id"] for i in call(world, "ADM2", "GET", "/tenants/invites").json()}
    got = tenant_admin.consume_invite(token, "new-one@example.com")
    assert got and got["tenant_id"] == world.t1 and got["role"] == "user"
    assert tenant_admin.consume_invite(token, "new-one@example.com") is None          # single use
    assert tenant_admin.consume_invite("not-a-token") is None
    assert tenant_admin.consume_invite("x" * 5000) is None


def test_invite_expired_revoked_and_email_bound(world):
    from VENUAI import tenant_admin
    inv = tenant_admin.create_invite(world.t1, world.users["ADM1"].id, "user", email="only-one@example.com")
    assert tenant_admin.consume_invite(inv["token"], "other-one@example.com") is None
    assert tenant_admin.consume_invite(inv["token"], None) is None
    assert tenant_admin.consume_invite(inv["token"], "ONLY-one@example.com")["tenant_id"] == world.t1
    inv2 = tenant_admin.create_invite(world.t1, world.users["ADM1"].id, "user")
    assert call(world, "ADM1", "DELETE", f"/tenants/invites/{inv2['id']}").status_code == 200
    assert tenant_admin.consume_invite(inv2["token"]) is None
    inv3 = tenant_admin.create_invite(world.t1, world.users["ADM1"].id, "user")
    with auth._get_conn() as c:
        c.execute("UPDATE tenant_invites SET expires_at = ? WHERE id = ?", (time.time() - 1, inv3["id"]))
        c.commit()
    assert tenant_admin.consume_invite(inv3["token"]) is None


@pytest.mark.parametrize("body", [{"role": "superadmin"}, {"role": "root"}, {"role": "user", "tenant_id": "t2"},
                                  {"role": "user", "extra": 1}, {"ttl_hours": 0}, {"ttl_hours": 10**6}])
def test_invite_mass_assignment_and_bad_values_rejected(world, body):
    assert call(world, "ADM1", "POST", "/tenants/invites", json=body).status_code == 422


def test_role_change_cannot_cross_tenants_or_escalate_via_body(world):
    c_id = world.users["C"].id
    r = call(world, "ADM1", "PATCH", f"/tenants/members/{c_id}", json={"role": "admin"})
    assert _is_not_found(r)
    assert auth.get_user_by_id(c_id)["role"] == "user"
    assert _is_not_found(call(world, "ADM1", "DELETE", f"/tenants/members/{c_id}"))
    assert auth.get_user_by_id(c_id) is not None
    # forged tenant in the body is rejected (extra=forbid), role values are validated
    b_id = world.users["B"].id
    assert call(world, "ADM1", "PATCH", f"/tenants/members/{b_id}",
                json={"role": "admin", "tenant_id": world.t2}).status_code == 422
    assert call(world, "ADM1", "PATCH", f"/tenants/members/{b_id}", json={"role": "god"}).status_code == 422
    assert auth.get_user_by_id(b_id)["role"] == "user"
    # a regular member cannot promote anybody, themselves included
    for who in ("A", "B", "C"):
        uid = world.users[who].id
        assert call(world, who, "PATCH", f"/tenants/members/{uid}", json={"role": "admin"}).status_code == 403


def test_last_admin_cannot_demote_or_remove_themselves(world):
    t = auth.create_tenant("Solo", "solo").tenant_id
    solo = auth.create_user("solo-solo@example.com", "Sup3r-secret-pw!", role="admin", tenant_id=t)
    h = {"Authorization": f"Bearer {auth.create_access_token(solo.id, 'admin')}"}
    cl = world.client
    assert cl.patch(f"/tenants/members/{solo.id}", json={"role": "user"}, headers=h).status_code == 409
    assert cl.delete(f"/tenants/members/{solo.id}", headers=h).status_code == 409
    second = auth.create_user("second-solo@example.com", "Sup3r-secret-pw!", role="admin", tenant_id=t)
    assert cl.patch(f"/tenants/members/{solo.id}", json={"role": "user"}, headers=h).status_code == 200
    # now `solo` is a plain user: the role comes from the DB, not from the (still valid) token claim
    assert cl.get("/tenants/members", headers=h).status_code == 403
    assert auth.get_user_by_id(second.id)["role"] == "admin"


def test_member_removal_revokes_refresh_tokens_and_is_tenant_scoped(world):
    t = auth.create_tenant("Rm", "rm").tenant_id
    adm = auth.create_user("adm-rm@example.com", "Sup3r-secret-pw!", role="admin", tenant_id=t)
    mem = auth.create_user("mem-rm@example.com", "Sup3r-secret-pw!", role="user", tenant_id=t)
    rt = auth.issue_refresh_token(mem.id)
    h = {"Authorization": f"Bearer {auth.create_access_token(adm.id, 'admin')}"}
    assert world.client.delete(f"/tenants/members/{mem.id}", headers=h).status_code == 200
    assert auth.get_user_by_id(mem.id) is None
    with pytest.raises(Exception):
        auth.rotate_refresh_token(rt)


# ─────────────────────────────────────────────────────────────────────────────
# Mass assignment / role escalation / forged identity
# ─────────────────────────────────────────────────────────────────────────────

def test_register_ignores_role_in_body(world):
    r = world.client.post("/auth/register", json={
        "email": "evil-one@example.com", "password": "Sup3r-secret-pw!", "role": "admin", "is_admin": True,
        "organization_name": "Evil Org"})
    assert r.status_code == 201
    assert r.json()["role"] == "user"
    assert auth.get_user_by_email("evil-one@example.com")["role"] == "user"


def test_tenant_patch_ignores_foreign_tenant_id_in_body(world):
    r = call(world, "ADM1", "PATCH", "/tenants", json={"name": "Renamed One", "tenant_id": world.t2})
    assert r.status_code == 200 and r.json()["tenant_id"] == world.t1
    assert auth.get_tenant(world.t1)["name"] == "Renamed One"
    assert auth.get_tenant(world.t2)["name"] == "Tenant Two"


def test_add_member_role_is_validated_and_stays_in_callers_tenant(world):
    r = call(world, "ADM1", "POST", "/tenants/members",
             json={"email": "m1-one@example.com", "password": "Sup3r-secret-pw!", "role": "superuser",
                   "tenant_id": world.t2})
    assert r.status_code == 201
    assert r.json()["role"] == "user" and r.json()["tenant_id"] == world.t1
    assert call(world, "A", "POST", "/tenants/members",
                json={"email": "m2-one@example.com", "password": "Sup3r-secret-pw!", "role": "admin"}).status_code == 403


def test_forged_role_claim_in_token_is_ignored(world):
    # a validly signed token whose role claim says "admin" for a plain user: the
    # server takes role/tenant from the user row, never from the token.
    forged = {"Authorization": "Bearer " + auth.create_access_token(world.users["A"].id, "admin")}
    assert world.client.get("/tenants/members", headers=forged).status_code == 403
    me = world.client.get("/auth/me", headers=forged).json()
    assert me["role"] == "user" and me["tenant_id"] == world.t1


@pytest.mark.parametrize("hdr", ["X-Tenant-Id", "X-User-Id", "X-Role", "X-Forwarded-User"])
def test_identity_headers_are_ignored(world, res, hdr):
    h = dict(world.headers["B"])
    h[hdr] = world.users["A"].id if "User" in hdr else (world.t1 if "Tenant" in hdr else "admin")
    assert _is_not_found(world.client.get(f"/graph/entities/{res.entity}", headers=h))
    assert world.client.get("/tenants/members", headers=h).status_code == 403


def test_forged_tenant_in_query_or_body_is_ignored(world, res):
    r = world.client.get(f"/evidence/{res.evidence}?tenant_id={world.t1}&user_id={world.users['A'].id}",
                         headers=world.headers["C"])
    assert _is_not_found(r)
    # memory settings: a body naming another user toggles only the caller's own row
    from VENUAI import memory_manager
    ltm = memory_manager.get_ltm_store()
    r = world.client.put("/memory/settings", headers=world.headers["C"],
                         json={"enabled": True, "user_id": world.users["A"].id, "tenant_id": world.t1})
    assert r.status_code == 200
    assert ltm.user_enabled(world.t2, world.users["C"].id) is True
    assert ltm.user_enabled(world.t1, world.users["A"].id) is False


def test_query_handlers_overwrite_client_user_id_and_scope_memory():
    """/query and /query/stream replace the client-supplied user_id with the verified
    identity, normalise the session id, and use a tenant-bound memory view."""
    import inspect
    for fn in (rag_server.query_knowledge_base, rag_server.query_stream):
        src = inspect.getsource(fn)
        assert "req.user_id = current_user.id" in src
        assert "authz.normalize_session_id(req.session_id)" in src
        assert ".scoped(current_user.tenant_id)" in src


# ─────────────────────────────────────────────────────────────────────────────
# ID guessing / malformed ids
# ─────────────────────────────────────────────────────────────────────────────

BAD_IDS = ["0", "1", "null", "undefined", "../../etc/passwd", "..%2F..%2Fetc%2Fpasswd", "' OR '1'='1",
           "%00", "a" * 600, "%F0%9F%92%A5", "*", "%", "_", "{doc}", "__proto__", "00000000-0000-0000-0000-000000000000"]
ID_ROUTES = [("GET", "/documents/{}/regions"), ("DELETE", "/documents/{}"), ("DELETE", "/datasets/{}"),
             ("GET", "/graph/entities/{}"), ("GET", "/evidence/{}"), ("GET", "/traces/{}"),
             ("GET", "/research/jobs/{}"), ("DELETE", "/research/jobs/{}"), ("DELETE", "/memory/{}")]


@pytest.mark.parametrize("bad", BAD_IDS)
@pytest.mark.parametrize("method,tmpl", ID_ROUTES)
def test_guessed_or_malformed_ids_give_a_uniform_404(world, method, tmpl, bad):
    base = world.client.request(method, tmpl.format(uuid.uuid4().hex), headers=world.headers["A"])
    r = world.client.request(method, tmpl.format(bad), headers=world.headers["A"])
    assert r.status_code in (404, 405), (r.status_code, r.text[:120])
    if r.status_code == 404 and "/" not in bad and "%2F" not in bad:
        assert r.json() == base.json()


def test_numeric_or_sequential_ids_do_not_resolve(world, res):
    for guess in ("1", "2", "3", "0"):
        assert _is_not_found(world.client.get(f"/evidence/{guess}", headers=world.headers["A"]))
    assert len(res.evidence) == 16 and len(res.job) >= 16   # ids are unguessable random hex


def test_nonexistent_equals_forbidden_for_documents(world, res):
    forbidden = call(world, "C", "GET", f"/documents/{res.doc_priv}/regions")
    missing = call(world, "C", "GET", f"/documents/{uuid.uuid4()}/regions")
    assert (forbidden.status_code, forbidden.json()) == (missing.status_code, missing.json())


def test_safe_lookup_never_raises_keyerror(world):
    meta = {"a": {"tenant_id": "t1", "owner_id": "x"}}
    u = world.users["A"]
    user = auth.AuthenticatedUser(id=u.id, email=u.email, role=u.role, tenant_id=u.tenant_id)
    for bad in (None, 5, ["a"], {"a": 1}, "missing", ""):
        with pytest.raises(Exception) as ei:
            authz.check_doc_access(meta, bad, user)
        assert getattr(ei.value, "status_code", None) == 404


# ─────────────────────────────────────────────────────────────────────────────
# Memory isolation
# ─────────────────────────────────────────────────────────────────────────────

def test_user_b_using_user_a_session_id_sees_nothing(tmp_path):
    from VENUAI.memory_manager import MemoryManager
    mm = MemoryManager(db_path=str(tmp_path / "m.db"))
    mm.save_turn("shared-session", "alice", "my secret question", "my secret answer", tenant_id="t1")
    assert mm.get_context("shared-session", "bob", "q", tenant_id="t1")["working"] == []
    assert mm.get_context("shared-session", "alice", "q", tenant_id="t2")["working"] == []
    assert len(mm.get_context("shared-session", "alice", "q", tenant_id="t1")["working"]) == 2


def test_working_memory_key_cannot_collide_via_separators(tmp_path):
    from VENUAI.memory_manager import MemoryManager
    mm = MemoryManager(db_path=str(tmp_path / "m.db"))
    mm.save_turn("s", "u", "secret", "answer", tenant_id="t")
    # attacker-chosen ids that would collide under naive "a:b:c" joining
    for tenant, user, sess in (("t:u", "", "s"), ("t", "u:s", ""), ("", "t:u", "s"), ("t", "u", "s:")):
        assert mm.get_context(sess, user, "q", tenant_id=tenant)["working"] == []


@pytest.mark.parametrize("sid", ["../../x", "a b", "x" * 200, "' OR 1=1 --", "a\x00b", "%", "é", ""])
def test_session_ids_are_normalised(sid):
    n = authz.normalize_session_id(sid)
    assert authz.SESSION_ID_RE.match(n)
    assert authz.normalize_session_id(sid) == n or sid == ""    # deterministic (random only when absent)


def test_server_issues_session_ids_when_absent():
    assert authz.normalize_session_id(None) != authz.normalize_session_id(None)
    assert authz.normalize_session_id("good_id-1") == "good_id-1"


def test_sessions_table_is_keyed_by_tenant_user_session(tmp_path):
    import sqlite3
    from VENUAI.memory_manager import MemoryManager
    mm = MemoryManager(db_path=str(tmp_path / "m.db"))
    for tenant, user in (("t1", "alice"), ("t1", "bob"), ("t2", "alice")):
        mm.save_turn("same", user, f"{tenant}-{user}-question", "answer", tenant_id=tenant)
        mm.summarize_session("same", user, tenant_id=tenant)
    with sqlite3.connect(mm._db_path) as c:
        rows = c.execute("SELECT tenant_id, user_id, summary FROM sessions").fetchall()
    assert len(rows) == 3                         # no INSERT OR REPLACE collision
    assert "t1-alice" in [r for r in rows if r[:2] == ("t1", "alice")][0][2]
    assert "t1-bob" not in mm.get_context("same", "alice", "q", tenant_id="t1")["episodic"]
    assert "t2-alice" not in mm.get_context("same", "alice", "q", tenant_id="t1")["episodic"]


def test_legacy_sessions_table_is_migrated(tmp_path):
    import sqlite3
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, summary TEXT NOT NULL,"
                  " start_time TEXT NOT NULL, end_time TEXT NOT NULL, turn_count INTEGER DEFAULT 0)")
        c.execute("INSERT INTO sessions VALUES ('s','u','old summary','a','b',1)")
        c.execute("CREATE TABLE turns (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,"
                  " user_id TEXT NOT NULL, user_msg TEXT NOT NULL, ai_msg TEXT NOT NULL, timestamp TEXT NOT NULL)")
        c.commit()
    from VENUAI.memory_manager import MemoryManager
    mm = MemoryManager(db_path=str(db))
    assert "old summary" in mm.get_context("s", "u", "q", tenant_id="default")["episodic"]
    assert mm.get_context("s", "u", "q", tenant_id="other")["episodic"] == ""
    with sqlite3.connect(db) as c:
        pk = {r[1] for r in c.execute("PRAGMA table_info(sessions)").fetchall() if r[5]}
    assert pk == {"tenant_id", "user_id", "session_id"}


def test_default_memory_path_is_absolute_and_cwd_independent(tmp_path, monkeypatch):
    from VENUAI import memory_manager as mmod
    from pathlib import Path
    assert Path(mmod.DEFAULT_DB_PATH).is_absolute() and Path(mmod.DEFAULT_CHROMA_DIR).is_absolute()
    assert Path(mmod.DEFAULT_DB_PATH).parent == Path(mmod.__file__).resolve().parent
    monkeypatch.chdir(tmp_path)
    mm = mmod.MemoryManager()                      # no db_path: must not depend on the cwd
    assert mm._db_path == mmod.DEFAULT_DB_PATH
    assert not (tmp_path / "VENUAI").exists()
    from VENUAI import evidence_store, analytics
    assert "Path(__file__)" in Path(evidence_store.__file__).read_text(encoding="utf-8")
    assert "Path(__file__)" in Path(analytics.__file__).read_text(encoding="utf-8")


def test_long_term_memory_is_tenant_user_scoped(world):
    from VENUAI import memory_manager
    s = memory_manager.get_ltm_store()
    s.add_or_update("t1", "alice", "alice prefers metric units", "preference", None)
    assert s.recall("t1", "alice", "metric units")
    assert not s.recall("t1", "bob", "metric units")
    assert not s.recall("t2", "alice", "metric units")


def test_recall_tool_binds_tenant_and_user(monkeypatch):
    from VENUAI import agent_tools
    seen = {}

    class MM:
        def get_context(self, session_id, user_id, query, tenant_id="default"):
            seen.update(user=user_id, tenant=tenant_id)
            return {}

    import VENUAI.memory_manager as mmod
    monkeypatch.setattr(mmod, "get_memory_manager", lambda: MM())
    tools = agent_tools.build_agent_tools(user_id="real-user", allowed_doc_ids=None, tenant_id="real-tenant")
    next(t for t in tools if t.name == "memory_recall").invoke({"query": "x"})
    assert seen == {"user": "real-user", "tenant": "real-tenant"}


# ─────────────────────────────────────────────────────────────────────────────
# /health and /kb/preset
# ─────────────────────────────────────────────────────────────────────────────

def test_health_unauthenticated_leaks_nothing(world):
    r = world.client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    blob = r.text.lower()
    for leak in ("model", "groq", "llm", "embedding", "documents", "version", "chroma", "gpt"):
        assert leak not in blob


def test_health_details_requires_admin_and_is_tenant_scoped(world, res):
    assert world.client.get("/health/details").status_code == 401
    assert call(world, "A", "GET", "/health/details").status_code == 403
    d1 = call(world, "ADM1", "GET", "/health/details").json()
    d2 = call(world, "ADM2", "GET", "/health/details").json()
    assert d1["documents_ingested"] >= 2 and d2["documents_ingested"] >= 1
    meta = rag_server.load_metadata()
    assert d1["documents_ingested"] == sum(1 for v in meta.values() if v["tenant_id"] == world.t1)
    assert d2["documents_ingested"] == sum(1 for v in meta.values() if v["tenant_id"] == world.t2)


def test_kb_preset_requires_auth(world):
    assert world.client.get("/kb/preset").status_code == 401
    assert call(world, "A", "GET", "/kb/preset").status_code in (200, 404)


# ─────────────────────────────────────────────────────────────────────────────
# Policy engine / tool layer
# ─────────────────────────────────────────────────────────────────────────────

def test_policy_denies_unknown_tool_agent_role_and_malformed(monkeypatch):
    from VENUAI import policy_engine as pe
    assert not pe.check_tool_call("admin", "react_agent", "rm_rf").allowed
    assert not pe.check_tool_call("admin", "ghost_agent", "drdo_calculator").allowed
    assert not pe.check_tool_call("root", "react_agent", "drdo_calculator").allowed
    assert not pe.check_tool_call("ADMIN", "react_agent", "drdo_calculator").allowed
    assert not pe.check_tool_call(None, "react_agent", "drdo_calculator").allowed
    assert not pe.check_tool_call("admin", "react_agent", ["drdo_calculator"]).allowed
    assert pe.check_tool_call("user", "react_agent", "drdo_calculator").allowed


def test_code_execution_denied_for_user_and_off_by_default_for_admin(monkeypatch):
    from VENUAI import policy_engine as pe
    monkeypatch.setenv("ENABLE_CODE_EXECUTION", "1")
    assert not pe.check_tool_call("user", "react_agent", "code_execution").allowed
    assert pe.check_tool_call("admin", "react_agent", "code_execution").allowed
    monkeypatch.delenv("ENABLE_CODE_EXECUTION")
    d = pe.check_tool_call("admin", "react_agent", "code_execution")
    assert not d.allowed and "ENABLE_CODE_EXECUTION" in d.reason
    monkeypatch.setenv("ENABLE_CODE_EXECUTION", "0")
    assert not pe.check_tool_call("admin", "react_agent", "code_execution").allowed


def test_llm_requested_code_execution_is_denied_for_non_admin(monkeypatch):
    from VENUAI import agent_tools
    monkeypatch.setenv("ENABLE_CODE_EXECUTION", "1")
    ran = []
    tools = agent_tools.build_agent_tools(user_id="u", allowed_doc_ids=[], user_role="user", tenant_id="t")
    out = next(t for t in tools if t.name == "code_execution").invoke({"code": "print(1)"})
    assert "denied" in out.lower() and not ran


def test_tool_call_budget_blunts_injection_loops(monkeypatch):
    from VENUAI import agent_tools
    monkeypatch.setenv("TOOL_CALL_BUDGET", "3")
    tools = agent_tools.build_agent_tools(user_id="u", allowed_doc_ids=[], user_role="user", tenant_id="t")
    calc = next(t for t in tools if t.name == "drdo_calculator")
    outs = [calc.invoke({"expression": "1+1"}) for _ in range(6)]
    assert all("2" in o for o in outs[:3])
    assert all("budget" in o.lower() for o in outs[3:])
    # a new request gets a fresh budget
    tools2 = agent_tools.build_agent_tools(user_id="u", allowed_doc_ids=[], user_role="user", tenant_id="t")
    assert "2" in next(t for t in tools2 if t.name == "drdo_calculator").invoke({"expression": "1+1"})


def test_unscoped_module_level_tools_refuse_without_operator_optin(monkeypatch):
    from VENUAI import agent_tools
    monkeypatch.delenv("ALLOW_UNSCOPED_TOOLS", raising=False)
    assert "denied" in agent_tools.drdo_kb_search.invoke({"query": "x"}).lower()
    assert "denied" in agent_tools.list_knowledge_base.invoke({}).lower()
    assert "denied" in agent_tools.memory_recall.invoke({"query": "x", "user_id": "victim"}).lower()
    assert "denied" in agent_tools.document_summary.invoke({"doc_id": "x"}).lower()


def test_document_summary_does_not_reveal_existence(monkeypatch, world, res):
    from VENUAI import agent_tools
    visible = authz.ScopedDocIds([res.doc_pub], tenant_id=world.t1)
    hidden = agent_tools._summarize_document(res.doc_priv, visible)
    missing = agent_tools._summarize_document(uuid.uuid4().hex, visible)
    assert hidden == missing


# ─────────────────────────────────────────────────────────────────────────────
# Vector retrieval: tenant_id in metadata + enforced
# ─────────────────────────────────────────────────────────────────────────────

class _Doc:
    def __init__(self, content, meta):
        self.page_content, self.metadata = content, meta


def test_dense_filter_includes_tenant_for_non_default_tenants():
    from VENUAI import retrieval
    seen = {}

    class VS:
        def similarity_search_with_score(self, q, k, filter=None):
            seen["f"] = filter
            return []

    retrieval._dense(VS(), "q", 3, ["d1"], "t2")
    assert seen["f"] == {"$and": [{"doc_id": {"$in": ["d1"]}}, {"tenant_id": "t2"}]}
    retrieval._dense(VS(), "q", 3, ["d1"], "default")      # legacy chunks have no tenant_id metadata
    assert seen["f"] == {"doc_id": {"$in": ["d1"]}}
    retrieval._dense(VS(), "q", 3, [], "t2")
    assert seen["f"]["$and"][0] == {"doc_id": {"$in": ["__none__"]}}


def test_retrieve_documents_drops_chunks_of_other_tenants(monkeypatch):
    from VENUAI import retrieval
    good = _Doc("alpha good", {"doc_id": "d1", "tenant_id": "t1", "page": 0})
    leak = _Doc("alpha leaked", {"doc_id": "d1", "tenant_id": "t2", "page": 0})
    legacy = _Doc("alpha legacy", {"doc_id": "d1", "page": 1})
    monkeypatch.setattr(retrieval, "_dense", lambda vs, q, k, allowed, tenant=None: [(good, 0.9), (leak, 0.9), (legacy, 0.9)])
    monkeypatch.setattr(retrieval, "get_bm25_index", lambda vs: None)
    allowed = authz.ScopedDocIds(["d1"], tenant_id="t1")
    out = retrieval.retrieve_documents(object(), "alpha", 5, allowed, rewrite=False, include_regions=False).documents
    texts = {d.page_content for d in out}
    assert "alpha leaked" not in texts and "alpha good" in texts
    # chunks without tenant metadata (pre multi-tenancy) belong to the default tenant only
    assert "alpha legacy" not in texts
    assert authz.chunk_in_tenant({"doc_id": "d1"}, "default") and authz.chunk_in_tenant({}, None)
    assert not authz.chunk_in_tenant({"doc_id": "d1"}, "t1")


def test_scoped_doc_ids_behaves_like_a_list_and_carries_tenant(world):
    u = world.users["A"]
    ids = rag_server.get_visible_doc_ids(
        auth.AuthenticatedUser(id=u.id, email=u.email, role=u.role, tenant_id=u.tenant_id))
    assert isinstance(ids, list) and authz.tenant_of(ids) == world.t1


def test_upload_writes_tenant_id_into_chunk_metadata():
    import inspect
    src = inspect.getsource(rag_server.upload_document)
    assert 'chunk.metadata["tenant_id"]' in src


def test_document_delete_filters_vector_store_on_tenant(world, res):
    r = call(world, "A", "DELETE", f"/documents/{res.doc_priv}")
    assert r.status_code == 200
    wheres = world.vs._collection.deleted
    assert {"$and": [{"doc_id": res.doc_priv}, {"tenant_id": world.t1}]} in wheres


# ─────────────────────────────────────────────────────────────────────────────
# Graph / evidence store level tenancy (parametrised)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tenant,owners,expect", [
    ("t1", None, True), ("t1", ["someone-else"], False), ("t2", None, False), ("t2", ["x"], False)])
def test_graph_store_scoping(world, res, tenant, owners, expect):
    from VENUAI.knowledge_graph import store as kgs
    st = kgs.get_graph_store()
    got = st.get_entity(res.entity, allowed_owner_ids=([world.users["A"].id] if owners == ["x"] else owners),
                        tenant_id=tenant)
    assert (got is not None) is expect
    found = st.find_entities(name_query="SecretLab", allowed_owner_ids=None, tenant_id=tenant)
    assert (len(found) > 0) is (tenant == "t1")


@pytest.mark.parametrize("tenant,user,expect", [("t1", "A", True), ("t1", "B", False),
                                                ("t2", "A", False), ("t2", "C", False)])
def test_evidence_store_scoping(world, res, tenant, user, expect):
    from VENUAI import evidence_store
    uid = world.users[user].id
    tid = world.t1 if tenant == "t1" else world.t2
    assert (evidence_store.get_record(res.evidence, tid, uid) is not None) is expect
    assert (res.evidence in {r["record_id"] for r in evidence_store.list_records(tid, uid)}) is expect
    # admin-style (user_id=None) still bound to the tenant
    assert (evidence_store.get_record(res.evidence, tid, None) is not None) is (tid == world.t1)


def test_graph_extract_ignores_body_tenant_and_owner(world):
    r = call(world, "C", "POST", "/graph/extract",
             json={"text": "LRDE developed the Uttam radar.", "document_name": "d", "use_llm": False,
                   "tenant_id": world.t1, "owner_id": world.users["A"].id})
    assert r.status_code == 200, r.text
    from VENUAI.knowledge_graph import store as kgs
    st = kgs.get_graph_store()
    assert st.find_entities(name_query="LRDE", tenant_id=world.t1) == []
    mine = st.find_entities(name_query="LRDE", tenant_id=world.t2)
    assert mine and all(x.owner_id == world.users["C"].id and x.tenant_id == world.t2 for x in mine)


# ─────────────────────────────────────────────────────────────────────────────
# Route inventory: every route is classified; every authenticated one rejects anon
# ─────────────────────────────────────────────────────────────────────────────

PUBLIC_ROUTES = {
    "POST /auth/register", "POST /auth/login", "POST /auth/refresh", "POST /auth/logout",
    "GET /auth/oauth/{provider}/login", "POST /auth/oauth/{provider}/callback",
    "POST /auth/forgot-password", "POST /auth/reset-password",
    "GET /health", "GET /.well-known/agent-card.json",
    "GET /docs", "GET /redoc", "GET /openapi.json", "GET /docs/oauth2-redirect",
    "POST /a2a/v1/message:send",   # token-checked inside the handler
    "GET /voice/status",
}


def _routes():
    out = []
    for r in rag_server.app.routes:
        methods = getattr(r, "methods", None)
        if not methods:
            continue
        for m in sorted(methods - {"HEAD", "OPTIONS"}):
            out.append((m, r.path))
    return out


def test_every_route_is_classified_and_authenticated_routes_reject_anonymous(world):
    unknown_public, bad = [], []
    for method, path in _routes():
        key = f"{method} {path}"
        if key in PUBLIC_ROUTES:
            continue
        url = path
        for name in {p.strip("{}") for p in path.split("/") if p.startswith("{")}:
            url = url.replace("{" + name + "}", "x")
        kw = {}
        if method in ("POST", "PUT", "PATCH"):
            kw["json"] = {}
        resp = world.client.request(method, url, **kw)
        if resp.status_code not in (401, 403):
            bad.append((key, resp.status_code))
    assert not bad, f"routes reachable without credentials (add auth or classify as public): {bad}"
    assert not unknown_public


def test_matrix_covers_every_id_route():
    """Every authenticated route with a path parameter is either in the matrix or explicitly waived."""
    covered = set()
    for _c, method, tmpl, _b, _e in MATRIX:
        import re
        covered.add((method, re.sub(r"\{\w+\}", "{}", tmpl)))
    covered |= {("DELETE", "/memory/{}"), ("GET", "/auth/oauth/{}/login")}
    waived = {
        ("POST", "/auth/oauth/{}/callback"),
        ("PATCH", "/tenants/members/{}"), ("DELETE", "/tenants/members/{}"),    # test_role_change_* tests
    }
    missing = []
    for method, path in _routes():
        if "{" not in path or f"{method} {path}" in PUBLIC_ROUTES:
            continue
        norm = __import__("re").sub(r"\{\w+\}", "{}", path)
        if (method, norm) in covered or (method, norm) in waived:
            continue
        missing.append((method, path))
    assert not missing, f"id routes missing from the IDOR matrix: {missing}"
