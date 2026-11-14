import pytest

from VENUAI.tests.security.fixtures import *  # noqa: F401,F403

pytestmark = pytest.mark.security


def test_two_tenants_are_isolated(two_tenants):
    assert two_tenants.alice.tenant_id == two_tenants.carol.tenant_id
    assert two_tenants.alice.tenant_id != two_tenants.bob.tenant_id
    assert two_tenants.admin.role == "admin"


def test_valid_token_accepted_forged_rejected(sec_client, two_tenants, forged_tokens):
    ok = sec_client.get("/auth/me", headers=two_tenants.alice.headers)
    assert ok.status_code == 200
    for name, tok in forged_tokens.items():
        r = sec_client.get("/auth/me", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code in (401, 403), f"{name} token accepted: {r.status_code}"


def test_cors_preflight_from_evil_origin_not_allowed(sec_client, evil_origin):
    r = sec_client.options(
        "/auth/login",
        headers={"Origin": evil_origin, "Access-Control-Request-Method": "POST"},
    )
    assert r.headers.get("access-control-allow-origin") != evil_origin
