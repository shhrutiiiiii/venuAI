"""
HTTP hardening for the FastAPI app (security stream S4).

Everything here is wired in by one call, `register_security(app, limiter)`, so
rag_server.py keeps a thin hook (see ADR-S4.1 / Documents/Backend/http-security.md):

* SecurityMiddleware (pure ASGI, so SSE streams are never buffered):
    - request id (X-Request-ID response header + log contextvar)
    - security headers on every response, including 4xx/5xx and CORS preflights
    - request body size limits (Content-Length *and* streamed byte counting)
    - last-resort exception catch -> generic 500 + request id (no str(e) leak)
* RateLimitMiddleware: per-user (JWT subject, no DB hit) falling back to
  per-IP global bucket, with higher *cost* and extra per-IP + daily buckets
  for the expensive LLM / upload routes.
* Strict CORS from ALLOWED_ORIGINS (explicit methods/headers).
* Global exception handlers (HTTPException, validation, 429, Exception).
* GET /capabilities - an authenticated route listing which optional feature
  routers are mounted (replaces the frontend's probe of /openapi.json, which is
  disabled in production).
* safe_error(): the one way to turn a caught exception into an HTTPException.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
from typing import Dict, List, Optional, Tuple

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from limits import parse as parse_limit
from limits.strategies import FixedWindowRateLimiter
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import ClientDisconnect

from VENUAI import config
from VENUAI.observability import new_request_id, request_id_var

logger = logging.getLogger("drdo-rag")

# ── Client identity ───────────────────────────────────────────────────────────

def _peer_ip(scope_client) -> str:
    return scope_client[0] if scope_client else "unknown"


def _is_trusted_peer(peer: str, trusted: List[str]) -> bool:
    if "*" in trusted:
        return True
    for t in trusted:
        try:
            if "/" in t:
                if ipaddress.ip_address(peer) in ipaddress.ip_network(t, strict=False):
                    return True
            elif peer == t or ipaddress.ip_address(peer) == ipaddress.ip_address(t):
                return True
        except ValueError:
            continue
    return False


def client_ip(request: Request) -> str:
    """Peer IP, or - only when the immediate peer is a configured TRUSTED_PROXY -
    the right-most X-Forwarded-For entry that is not itself a trusted proxy.
    A client-supplied X-Forwarded-For is ignored unless it arrives via a trusted hop."""
    peer = _peer_ip(request.scope.get("client"))
    trusted = config.trusted_proxies()
    if not trusted or not _is_trusted_peer(peer, trusted):
        return peer
    xff = request.headers.get("x-forwarded-for", "")
    hops = [h.strip() for h in xff.split(",") if h.strip()]
    for hop in reversed(hops):
        try:
            ipaddress.ip_address(hop)
        except ValueError:
            continue
        if not _is_trusted_peer(hop, trusted):
            return hop
    real = request.headers.get("x-real-ip", "").strip()
    try:
        ipaddress.ip_address(real)
        return real
    except ValueError:
        return peer


def authenticated_user_id(request: Request) -> Optional[str]:
    """JWT subject of a *valid* bearer token (signature + expiry checked, no DB)."""
    authz = request.headers.get("authorization", "")
    if not authz.lower().startswith("bearer "):
        return None
    try:
        from VENUAI import auth
        payload = auth.decode_access_token(authz[7:].strip())
        sub = payload.get("sub")
        return str(sub) if sub else None
    except Exception:  # noqa: BLE001 - invalid/expired token: treat as anonymous
        return None


def ip_key(request: Request) -> str:
    return f"ip:{client_ip(request)}"


def rate_key(request: Request) -> str:
    """slowapi key function: authenticated user id, else the (trusted-proxy aware) IP."""
    uid = authenticated_user_id(request)
    return f"u:{uid}" if uid else ip_key(request)


# ── Safe errors ───────────────────────────────────────────────────────────────

def safe_error(exc: BaseException, public_msg: str = "Request failed.", status_code: int = 500,
               headers: Optional[dict] = None) -> StarletteHTTPException:
    """Log `exc` (with traceback and request id) server-side and return an
    HTTPException carrying only `public_msg`. Usage: `raise safe_error(e, "Failed to parse PDF.", 422)`."""
    logger.error("%s [request_id=%s] %s", public_msg, request_id_var.get(), type(exc).__name__, exc_info=exc)
    return StarletteHTTPException(status_code=status_code, detail=public_msg, headers=headers)


def public_error_body(detail, status_code: int = 500) -> dict:
    return {"detail": detail, "request_id": request_id_var.get()}


# ── Security middleware ───────────────────────────────────────────────────────

_PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"
_API_CSP = "default-src 'none'; frame-ancestors 'none'"
_DOC_PATHS = ("/docs", "/redoc")
_UPLOAD_PREFIXES = ("/upload", "/datasets/upload", "/voice/", "/documents/upload")


class _BodyTooLarge(Exception):
    pass


def _is_upload_path(path: str) -> bool:
    return any(path == p.rstrip("/") or path.startswith(p if p.endswith("/") else p + "/")
               for p in _UPLOAD_PREFIXES)


class SecurityMiddleware:
    def __init__(self, app, max_body: Optional[int] = None, max_upload: Optional[int] = None):
        self.app = app
        self.max_body = max_body or config.max_body_bytes()
        self.max_upload = max_upload or config.max_upload_bytes()

    def _decorate(self, scope, message, rid: str) -> None:
        h = MutableHeaders(scope=message)
        path = scope.get("path", "")
        static = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Permissions-Policy": _PERMISSIONS_POLICY,
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Resource-Policy": "same-origin",
            "Cache-Control": "no-store",
            "X-Request-ID": rid,
        }
        if not path.startswith(_DOC_PATHS):
            static["Content-Security-Policy"] = _API_CSP
        proto = ""
        for k, v in scope.get("headers", []):
            if k == b"x-forwarded-proto":
                proto = v.decode("latin-1").lower()
        if config.is_production() or proto == "https":
            static["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        for k, v in static.items():
            if k not in h:
                h[k] = v
        if "server" in h:
            del h["server"]
        if path.startswith("/auth") and "pragma" not in h:
            h["Pragma"] = "no-cache"

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = new_request_id()
        scope.setdefault("state", {})["request_id"] = rid
        token = request_id_var.set(rid)
        started = False
        suppress = False
        too_large = False
        path = scope.get("path", "")
        limit = self.max_upload if _is_upload_path(path) else self.max_body

        async def respond(status: int, body: dict, extra: Optional[dict] = None):
            nonlocal started
            resp = JSONResponse(body, status_code=status, headers=extra)

            async def raw_send(message):
                if message["type"] == "http.response.start":
                    self._decorate(scope, message, rid)
                await send(message)

            started = True
            await resp(scope, receive, raw_send)

        async def send_w(message):
            nonlocal started, suppress
            if suppress:
                return
            if message["type"] == "http.response.start":
                if too_large:
                    suppress = True
                    await respond(413, {"detail": "Request body too large.", "request_id": rid})
                    return
                started = True
                self._decorate(scope, message, rid)
            await send(message)

        total = 0

        async def counted_receive():
            nonlocal total, too_large
            msg = await receive()
            if msg["type"] == "http.request":
                total += len(msg.get("body", b""))
                if total > limit:
                    too_large = True
                    raise _BodyTooLarge()
            return msg

        try:
            for k, v in scope.get("headers", []):
                if k == b"content-length":
                    try:
                        if int(v) > limit:
                            too_large = True
                            await respond(413, {"detail": "Request body too large.", "request_id": rid})
                            return
                    except ValueError:
                        await respond(400, {"detail": "Invalid Content-Length.", "request_id": rid})
                        return
            await self.app(scope, counted_receive, send_w)
        except _BodyTooLarge:
            if not started:
                await respond(413, {"detail": "Request body too large.", "request_id": rid})
        except ClientDisconnect:
            return
        except Exception as exc:  # noqa: BLE001 - last-resort net (inner handlers normally catch first)
            logger.error("Unhandled exception [request_id=%s] %s %s", rid, scope.get("method"), path, exc_info=exc)
            if not started:
                await respond(500, {"detail": "Internal server error", "request_id": rid})
            else:
                raise
        finally:
            request_id_var.reset(token)


# ── Rate limiting ─────────────────────────────────────────────────────────────
# (method, path regex, cost against the per-user bucket)
_EXPENSIVE: List[Tuple[str, "re.Pattern[str]", int]] = [
    ("POST", re.compile(r"^/query(/stream|/resume)?$"), 5),
    ("POST", re.compile(r"^/research(/.*)?$"), 10),
    ("POST", re.compile(r"^/graph/extract$"), 5),
    ("POST", re.compile(r"^/datasets/[^/]+/analyze$"), 5),
    ("POST", re.compile(r"^/documents/[^/]+/regions/[^/]+/ask$"), 5),
    ("POST", re.compile(r"^/a2a(/.*)?$"), 5),
    ("POST", re.compile(r"^/(upload(/multimodal)?|datasets/upload|voice/.*)$"), 3),
]
_RATE_EXEMPT = ("/health", "/docs", "/redoc", "/openapi.json")


def build_limiter() -> Limiter:
    storage = os.getenv("RATE_LIMIT_STORAGE_URI") or "memory://"
    return Limiter(key_func=rate_key, storage_uri=storage)


class RateLimitMiddleware:
    """Global per-user / per-IP limits with route cost, using the slowapi limiter's storage
    (so `limiter.reset()` and RATE_LIMIT_STORAGE_URI cover both layers)."""

    def __init__(self, app, limiter: Limiter):
        self.app = app
        self.strategy = FixedWindowRateLimiter(limiter._storage)
        self.default = parse_limit(os.getenv("RATE_LIMIT_DEFAULT", "120/minute"))
        self.llm_ip = parse_limit(os.getenv("RATE_LIMIT_LLM_IP", "90/minute"))
        self.llm_daily = parse_limit(os.getenv("RATE_LIMIT_LLM_DAILY", "1000/day"))

    async def _deny(self, scope, receive, send, item, what: str):
        retry = max(1, int(item.get_expiry()))
        resp = JSONResponse(
            {"detail": "Rate limit exceeded. Please slow down.", "error": "Rate limit exceeded",
             "request_id": request_id_var.get()},
            status_code=429, headers={"Retry-After": str(retry)})
        logger.warning("rate limit hit (%s) path=%s", what, scope.get("path"))
        await resp(scope, receive, send)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] == "OPTIONS" or scope["path"] in _RATE_EXEMPT:
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        uid = authenticated_user_id(request)
        key = f"u:{uid}" if uid else ip_key(request)
        cost = 1
        expensive = False
        for method, rx, c in _EXPENSIVE:
            if scope["method"] == method and rx.match(scope["path"]):
                cost, expensive = c, True
                break
        if not self.strategy.hit(self.default, "global", key, cost=cost):
            await self._deny(scope, receive, send, self.default, "global")
            return
        if expensive:
            ip = ip_key(request)
            if not self.strategy.hit(self.llm_ip, "llm-ip", ip):
                await self._deny(scope, receive, send, self.llm_ip, "llm-ip")
                return
            if uid and not self.strategy.hit(self.llm_daily, "llm-daily", key):
                await self._deny(scope, receive, send, self.llm_daily, "llm-daily")
                return
        await self.app(scope, receive, send)


# ── Exception handlers ────────────────────────────────────────────────────────

async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
    detail = exc.detail
    if exc.status_code >= 500 and not isinstance(detail, (dict, list)):
        # 5xx text is never trusted to be safe; the log has the cause.
        logger.error("HTTP %s [request_id=%s]: %s", exc.status_code, request_id_var.get(), detail)
        detail = detail if isinstance(detail, str) and len(detail) < 200 else "Internal server error"
    body = detail if isinstance(detail, dict) and "request_id" in detail else {"detail": detail, "request_id": request_id_var.get()}
    return JSONResponse(body, status_code=exc.status_code, headers=getattr(exc, "headers", None))


async def _validation_handler(request: Request, exc: RequestValidationError):
    # Field-safe: location + message + type only. Never echo `input`/`ctx` (could contain secrets).
    errors = [{"loc": list(e.get("loc", [])), "msg": str(e.get("msg", "Invalid value")), "type": e.get("type", "value_error")}
              for e in exc.errors()]
    return JSONResponse({"detail": errors, "request_id": request_id_var.get()}, status_code=422)


async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    retry = 60
    try:
        retry = max(1, int(exc.limit.limit.get_expiry()))
    except Exception:  # noqa: BLE001
        pass
    return JSONResponse(
        {"detail": "Rate limit exceeded. Please slow down.", "error": "Rate limit exceeded",
         "request_id": request_id_var.get()},
        status_code=429, headers={"Retry-After": str(retry)})


async def _unhandled_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception [request_id=%s] %s %s", request_id_var.get(), request.method, request.url.path, exc_info=exc)
    return JSONResponse({"detail": "Internal server error", "request_id": request_id_var.get()}, status_code=500)


# ── Capabilities (replaces the frontend's /openapi.json probe) ────────────────

def compute_capabilities(paths: List[str]) -> Dict[str, bool]:
    has = paths.__contains__
    matches = lambda rx: any(re.match(rx, p) for p in paths)  # noqa: E731
    return {
        "research_jobs": has("/research/jobs") and has("/research/jobs/plan"),
        "voice": has("/voice/status") and (has("/voice/transcribe") or has("/voice/speak")),
        "memory": has("/memory"),
        "traces": matches(r"^/traces/\{[^}]+\}$"),
        "approvals": has("/approvals/pending") and matches(r"^/approvals/\{[^}]+\}/decision$"),
        "export": has("/export/report"),
        "resume": has("/query/resume"),
    }


# ── Wiring ────────────────────────────────────────────────────────────────────

def register_security(app: FastAPI, limiter: Limiter) -> None:
    """Install CORS, rate limiting, headers/size middleware, handlers and /capabilities."""
    from VENUAI.auth import get_current_user

    # add_middleware is LIFO: the last added is outermost. Order (outer -> inner):
    # Security -> CORS -> RateLimit -> routes, so 429/413/500 all carry security + CORS headers.
    app.add_middleware(RateLimitMiddleware, limiter=limiter)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.allowed_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
        expose_headers=["X-Request-ID", "X-Trace-Id", "Retry-After"],
        max_age=600,
    )
    app.add_middleware(SecurityMiddleware)

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(Exception, _unhandled_handler)

    @app.get("/capabilities", include_in_schema=False)
    def capabilities(_user=Depends(get_current_user)) -> Dict[str, bool]:
        paths = [getattr(r, "path", "") for r in app.routes]
        return compute_capabilities(paths)
