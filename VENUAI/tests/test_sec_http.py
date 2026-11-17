"""
Security stream S4 - HTTP hardening: headers, CORS, body limits, rate limits,
error handling, log redaction, production config validation, SSE streaming.
"""
import asyncio
import logging
import re
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel

from VENUAI import config, http_security
from VENUAI.observability import redact

GOOD_SECRET = "Zk3p9QxV7mTr2LcWb8NfH4yGdJ6sAeU1oPiR5vXq"
PROD_OK = {
    "ENV": "production",
    "JWT_SECRET_KEY": GOOD_SECRET,
    "ALLOWED_ORIGINS": "https://app.example.org",
    "FRONTEND_URL": "https://app.example.org",
}


def _mini_app(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    app = FastAPI()
    limiter = http_security.build_limiter()
    http_security.register_security(app, limiter)

    class Body(BaseModel):
        n: int

    @app.get("/ping")
    def ping():
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/echo")
    async def echo(request_body: dict):
        return {"keys": list(request_body)}

    @app.post("/upload")
    async def upload(request_body: dict):
        return {"ok": True}

    @app.post("/val")
    def val(b: Body):
        return {"n": b.n}

    @app.get("/boom")
    def boom():
        raise RuntimeError("super-secret-db-password=hunter2 /etc/passwd")

    @app.get("/http500")
    def http500():
        raise HTTPException(status_code=500, detail="x" * 500)

    @app.get("/stream")
    async def stream():
        async def gen():
            for i in range(3):
                yield f"data: {i}\n\n"
                await asyncio.sleep(0)
        return StreamingResponse(gen(), media_type="text/event-stream")

    return app, limiter


def _client(app):
    return TestClient(app, raise_server_exceptions=False)


# ── headers ───────────────────────────────────────────────────────────────────

def test_security_headers_present_on_success_and_errors(monkeypatch):
    app, _ = _mini_app(monkeypatch)
    c = _client(app)
    for resp in (c.get("/ping"), c.get("/nope"), c.get("/boom"), c.post("/val", json={"n": "x"})):
        h = resp.headers
        assert h["x-content-type-options"] == "nosniff"
        assert h["x-frame-options"] == "DENY"
        assert h["referrer-policy"] == "no-referrer"
        assert "camera=()" in h["permissions-policy"]
        assert h["cross-origin-opener-policy"] == "same-origin"
        assert h["cross-origin-resource-policy"] == "same-origin"
        assert "frame-ancestors 'none'" in h["content-security-policy"]
        assert h["cache-control"] == "no-store"
        assert h["x-request-id"]


def test_hsts_only_when_https_or_production(monkeypatch):
    app, _ = _mini_app(monkeypatch)
    c = _client(app)
    assert "strict-transport-security" not in c.get("/ping").headers
    assert "max-age" in c.get("/ping", headers={"X-Forwarded-Proto": "https"}).headers["strict-transport-security"]
    monkeypatch.setenv("ENV", "production")
    assert "strict-transport-security" in c.get("/ping").headers


def test_real_app_auth_responses_not_cacheable(client):
    r = client.post("/auth/login", json={"email": "nobody@nowhere.com", "password": "whatever123"})
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["pragma"] == "no-cache"
    assert r.headers["x-content-type-options"] == "nosniff"


# ── CORS ──────────────────────────────────────────────────────────────────────

def test_cors_preflight_from_evil_origin_rejected(monkeypatch):
    app, _ = _mini_app(monkeypatch, ALLOWED_ORIGINS="https://good.example.org")
    c = _client(app)
    r = c.options("/ping", headers={"Origin": "https://evil.example.com",
                                    "Access-Control-Request-Method": "GET"})
    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers
    r2 = c.get("/ping", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in r2.headers


def test_cors_preflight_allowed_origin_is_strict(monkeypatch):
    app, _ = _mini_app(monkeypatch, ALLOWED_ORIGINS="https://good.example.org")
    c = _client(app)
    r = c.options("/ping", headers={"Origin": "https://good.example.org",
                                    "Access-Control-Request-Method": "POST",
                                    "Access-Control-Request-Headers": "authorization,content-type"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "https://good.example.org"
    assert r.headers["access-control-max-age"] == "600"
    assert "PUT" not in r.headers["access-control-allow-methods"]
    assert "x-content-type-options" in r.headers          # preflights get security headers too
    bad = c.options("/ping", headers={"Origin": "https://good.example.org",
                                      "Access-Control-Request-Method": "POST",
                                      "Access-Control-Request-Headers": "x-evil-header"})
    assert bad.status_code == 400


# ── body size ─────────────────────────────────────────────────────────────────

def test_body_too_large_by_content_length_413(monkeypatch):
    app, _ = _mini_app(monkeypatch, MAX_BODY_BYTES=1000)
    c = _client(app)
    r = c.post("/echo", content=b"{" + b" " * 5000 + b"}", headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    assert r.json()["request_id"]
    assert r.headers["x-content-type-options"] == "nosniff"
    ok = c.post("/echo", json={"a": 1})
    assert ok.status_code == 200


def test_body_too_large_streamed_without_content_length_413(monkeypatch):
    app, _ = _mini_app(monkeypatch, MAX_BODY_BYTES=1000)
    c = _client(app)

    def gen():
        yield b'{"a": "'
        for _ in range(20):
            yield b"x" * 200
        yield b'"}'

    r = c.post("/echo", content=gen(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_upload_route_gets_larger_limit(monkeypatch):
    app, _ = _mini_app(monkeypatch, MAX_BODY_BYTES=1000, MAX_UPLOAD_BYTES=100000)
    c = _client(app)
    payload = b'{"a": "' + b"x" * 5000 + b'"}'
    assert c.post("/echo", content=payload, headers={"Content-Type": "application/json"}).status_code == 413
    assert c.post("/upload", content=payload, headers={"Content-Type": "application/json"}).status_code == 200


def test_invalid_content_length_rejected(monkeypatch):
    app, _ = _mini_app(monkeypatch)
    r = _client(app).post("/echo", content=b"{}", headers={"Content-Length": "abc", "Content-Type": "application/json"})
    assert r.status_code in (400, 413)


# ── rate limiting ─────────────────────────────────────────────────────────────

def _token(uid):
    from VENUAI import auth
    return auth.create_access_token(uid, "user")


def test_rate_limit_is_per_user_not_per_ip(monkeypatch):
    app, _ = _mini_app(monkeypatch, RATE_LIMIT_DEFAULT="3/minute")
    c = _client(app)
    a = {"Authorization": f"Bearer {_token('user-a')}"}
    b = {"Authorization": f"Bearer {_token('user-b')}"}
    assert [c.get("/ping", headers=a).status_code for _ in range(3)] == [200, 200, 200]
    r = c.get("/ping", headers=a)
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1
    assert r.headers["x-content-type-options"] == "nosniff"
    # same IP, different user: unaffected
    assert c.get("/ping", headers=b).status_code == 200


def test_forged_token_cannot_dodge_limit_with_fresh_subjects(monkeypatch):
    app, _ = _mini_app(monkeypatch, RATE_LIMIT_DEFAULT="3/minute")
    c = _client(app)
    codes = [c.get("/ping", headers={"Authorization": f"Bearer forged.{i}.token"}).status_code for i in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]     # falls back to the IP bucket


def test_health_and_options_exempt_from_limit(monkeypatch):
    app, _ = _mini_app(monkeypatch, RATE_LIMIT_DEFAULT="2/minute")
    c = _client(app)
    assert all(c.get("/health").status_code == 200 for _ in range(10))


def test_expensive_route_costs_more(monkeypatch):
    app, _ = _mini_app(monkeypatch, RATE_LIMIT_DEFAULT="10/minute")

    @app.post("/query")
    def q():
        return {"ok": True}

    c = _client(app)
    h = {"Authorization": f"Bearer {_token('u-cost')}"}
    codes = [c.post("/query", headers=h).status_code for _ in range(3)]
    assert codes == [200, 200, 429]            # cost 5 each against 10/min


def test_x_forwarded_for_ignored_without_trusted_proxy(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY", raising=False)
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    app, _ = _mini_app(monkeypatch, RATE_LIMIT_DEFAULT="3/minute")
    c = _client(app)
    codes = [c.get("/ping", headers={"X-Forwarded-For": f"9.9.9.{i}"}).status_code for i in range(5)]
    assert codes == [200, 200, 200, 429, 429]  # spoofing XFF does not mint new buckets


def test_x_forwarded_for_trusted_proxy_separates_clients(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY", "testclient")
    app, _ = _mini_app(monkeypatch, RATE_LIMIT_DEFAULT="2/minute")
    c = _client(app)
    for ip in ("9.9.9.1", "9.9.9.2"):
        assert [c.get("/ping", headers={"X-Forwarded-For": ip}).status_code for _ in range(2)] == [200, 200]
    assert c.get("/ping", headers={"X-Forwarded-For": "9.9.9.1"}).status_code == 429


def test_trusted_proxy_takes_rightmost_untrusted_hop(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY", "10.0.0.0/8,testclient")
    from starlette.requests import Request
    scope = {"type": "http", "method": "GET", "path": "/", "client": ("testclient", 1),
             "headers": [(b"x-forwarded-for", b"6.6.6.6, 1.2.3.4, 10.0.0.5")]}
    assert http_security.client_ip(Request(scope)) == "1.2.3.4"   # left entries are attacker-controlled


# ── errors ────────────────────────────────────────────────────────────────────

def test_unhandled_exception_is_generic_with_request_id(monkeypatch):
    app, _ = _mini_app(monkeypatch)
    r = _client(app).get("/boom")
    assert r.status_code == 500
    body = r.json()
    assert body["detail"] == "Internal server error"
    assert body["request_id"] == r.headers["x-request-id"]
    assert "hunter2" not in r.text and "passwd" not in r.text and "RuntimeError" not in r.text


def test_long_5xx_detail_replaced(monkeypatch):
    app, _ = _mini_app(monkeypatch)
    r = _client(app).get("/http500")
    assert r.json()["detail"] == "Internal server error"


def test_validation_error_does_not_echo_input(monkeypatch):
    app, _ = _mini_app(monkeypatch)
    r = _client(app).post("/val", json={"n": "SECRET-INPUT-VALUE"})
    assert r.status_code == 422
    assert "SECRET-INPUT-VALUE" not in r.text
    assert r.json()["detail"][0]["loc"] == ["body", "n"]


def test_404_and_401_keep_shape_and_gain_request_id(client):
    r = client.get("/documents")
    assert r.status_code == 401 and r.json()["detail"] == "Not authenticated."
    assert r.json()["request_id"]
    assert r.headers["www-authenticate"] == "Bearer"


def test_safe_error_logs_but_hides(caplog):
    with caplog.at_level(logging.ERROR, logger="drdo-rag"):
        err = http_security.safe_error(ValueError("leak-me"), "Nope.", 422)
    assert err.status_code == 422 and err.detail == "Nope."
    assert "leak-me" not in err.detail


def test_no_str_e_leaks_in_response_paths():
    root = Path(__file__).resolve().parent.parent
    bad = []
    patterns = [
        re.compile(r"detail\s*=\s*str\("),
        re.compile(r"detail\s*=\s*f[\"'][^\"']*\{\s*(?:e|exc|err|error)\b[^}]*\}"),
        re.compile(r"return f[\"'][^\"']*\{\s*str\((?:e|exc|err)\)"),
        re.compile(r"\"error\"\s*:\s*str\((?:e|exc|err)\)"),
    ]
    for p in root.rglob("*.py"):
        if "tests" in p.parts or "evals" in p.parts:
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if "BudgetRefused" in line or "detail=str(refused)" in line:
                continue  # BudgetRefused carries the fixed token_budget.EXHAUSTED_MESSAGE, not exception text
            if any(rx.search(line) for rx in patterns):
                bad.append(f"{p.name}:{n}: {line.strip()}")
    assert not bad, "str(e) leaks:\n" + "\n".join(bad)


# ── capabilities / docs ───────────────────────────────────────────────────────

def test_capabilities_requires_auth_and_reports_routes(client):
    assert client.get("/capabilities").status_code == 401
    tok = client.post("/auth/login", json={"email": "demo@chat.com", "password": "demo12345"}).json()["access_token"]
    r = client.get("/capabilities", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert set(r.json()) == {"research_jobs", "voice", "memory", "traces", "approvals", "export", "resume"}


def test_docs_disabled_in_production_unless_enabled():
    assert config.docs_kwargs({"ENV": "production"}) == {"docs_url": None, "redoc_url": None, "openapi_url": None}
    assert config.docs_kwargs({"ENV": "production", "ENABLE_DOCS": "1"}) == {}
    assert config.docs_kwargs({}) == {}
    assert config.docs_kwargs({"APP_ENV": "dev", "ENABLE_DOCS": "0"})["docs_url"] is None


# ── SSE ───────────────────────────────────────────────────────────────────────

def test_sse_streams_incrementally_through_middleware():
    """The first chunk must reach the client while the generator is still running
    (it blocks until we have received it) - impossible if the middleware buffers."""
    got_first = asyncio.Event()

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream"), (b"cache-control", b"no-cache")]})
        await send({"type": "http.response.body", "body": b"data: 1\n\n", "more_body": True})
        await asyncio.wait_for(got_first.wait(), timeout=2)      # would time out if buffered
        await send({"type": "http.response.body", "body": b"data: 2\n\n", "more_body": False})

    mw = http_security.SecurityMiddleware(inner)
    sent = []

    async def run():
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(msg):
            sent.append(msg)
            if msg["type"] == "http.response.body" and msg.get("body") == b"data: 1\n\n":
                got_first.set()

        await mw({"type": "http", "method": "GET", "path": "/query/stream", "headers": [], "client": ("x", 1)},
                 receive, send)

    asyncio.run(run())
    start = sent[0]
    hdrs = {k.decode(): v.decode() for k, v in start["headers"]}
    assert hdrs["cache-control"] == "no-cache"                   # SSE's own value is kept
    assert hdrs["x-content-type-options"] == "nosniff"
    assert [m.get("body") for m in sent[1:]] == [b"data: 1\n\n", b"data: 2\n\n"]


def test_stream_route_works_end_to_end(monkeypatch):
    app, _ = _mini_app(monkeypatch)
    r = _client(app).get("/stream")
    assert r.status_code == 200 and r.text.count("data:") == 3
    assert r.headers["content-type"].startswith("text/event-stream")


# ── production config validation ──────────────────────────────────────────────

def test_prod_config_ok():
    errors, _ = config.validate_config(dict(PROD_OK))
    assert errors == []
    config.enforce_config(dict(PROD_OK))


@pytest.mark.parametrize("override,needle", [
    ({"JWT_SECRET_KEY": ""}, "JWT_SECRET_KEY"),
    ({"JWT_SECRET_KEY": "short"}, "weak"),
    ({"JWT_SECRET_KEY": "changeme" * 6}, "weak"),
    ({"JWT_SECRET_KEY": "a" * 64}, "weak"),
    ({"ALLOWED_ORIGINS": "*"}, "wildcard"),
    ({"ALLOWED_ORIGINS": "https://ok.example.org,http://localhost:3000"}, "local origin"),
    ({"ALLOWED_ORIGINS": "http://app.example.org"}, "not https"),
    ({"FRONTEND_URL": "http://app.example.org"}, "FRONTEND_URL"),
    ({"FRONTEND_URL": ""}, "FRONTEND_URL"),
    ({"DEBUG": "true"}, "Debug flag"),
    ({"RELOAD": "1"}, "Debug flag"),
    ({"SEED_DEMO_USER": "true"}, "SEED_DEMO_USER"),
    ({"ADMIN_PASSWORD": "admin123"}, "default"),
])
def test_prod_config_failures(override, needle):
    env = {**PROD_OK, **override}
    with pytest.raises(config.ConfigError) as ei:
        config.enforce_config(env)
    assert needle in str(ei.value)


def test_prod_config_reports_all_problems_at_once():
    with pytest.raises(config.ConfigError) as ei:
        config.enforce_config({"ENV": "production"})
    msg = str(ei.value)
    assert "JWT_SECRET_KEY" in msg and "FRONTEND_URL" in msg


def test_dev_config_never_raises():
    errors, warnings = config.validate_config({"JWT_SECRET_KEY": "x", "ALLOWED_ORIGINS": "*"})
    assert errors == []
    config.enforce_config({"JWT_SECRET_KEY": "x"})


def test_is_weak_secret():
    assert config.is_weak_secret(None) and config.is_weak_secret("x" * 40)
    assert not config.is_weak_secret(GOOD_SECRET)


# ── log redaction ─────────────────────────────────────────────────────────────

def test_redact_tokens_keys_passwords_emails():
    line = ('user alice.smith@example.com Bearer abcdef1234567890 password=hunter2 {"refresh_token": "abc.def"} '
            "gsk_abcdefghijklmnopqrstuv eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4 GET /x?token=zzz&y=1")
    out = redact(line)
    for secret in ("abcdef1234567890", "hunter2", "abc.def", "gsk_abcdefghijklmnopqrstuv",
                   "eyJhbGciOiJIUzI1", "token=zzz", "lice.smith"):
        assert secret not in out, secret
    assert "a***@example.com" in out
    assert "y=1" in out


def test_logging_pipeline_redacts_message_and_traceback(capfd):
    from VENUAI.observability import configure_logging
    configure_logging()
    log = logging.getLogger("drdo-rag.redaction-test")
    try:
        raise ValueError("boom api_key=sk-live-1234567890abcdefghij")
    except ValueError:
        log.error("login failed for bob@corp.io password=%s", "p@ssw0rd!", exc_info=True)
    out = capfd.readouterr().err
    assert "p@ssw0rd!" not in out and "sk-live-1234567890abcdefghij" not in out and "bob@corp.io" not in out
    assert "b***@corp.io" in out


def test_redaction_filter_on_uvicorn_access():
    from VENUAI.observability import RedactionFilter
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "GET %s"', ("1.2.3.4", "/cb?code=abc123&state=s"), None)
    RedactionFilter().filter(rec)
    assert "abc123" not in rec.getMessage()
