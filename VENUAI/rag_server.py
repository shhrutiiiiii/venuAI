"""
DRDO VenuAI — RAG Server
========================
FastAPI backend for the DRDO Knowledge Base RAG feature.

Endpoints:
  POST   /upload           – Ingest a PDF into ChromaDB
  POST   /query            – RAG query with Groq LLM + source citations
  GET    /documents        – List all ingested documents
  DELETE /documents/{id}   – Remove a document from the knowledge base
  GET    /health           – Health check
"""

import os
import sys
import time
import uuid
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root directory is in sys.path for VENUAI package imports
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Request, UploadFile, HTTPException, BackgroundTasks
from pydantic import BaseModel

from VENUAI import auth
from VENUAI.ai_gateway.provider import get_groq_client, get_chat_groq
from VENUAI.auth import AuthenticatedUser, get_current_user, require_admin
from VENUAI.upload_security import generate_storage_name, validate_upload

# ── Configuration ──────────────────────────────────────────────────────────────
load_dotenv()

from VENUAI.observability import configure_logging
configure_logging()  # LOG_FORMAT=json for structured output — see VENUAI/observability.py
logger = logging.getLogger("drdo-rag")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY not found in environment/.env")

# Primary model id (GROQ_MODEL overrides; the router puts it first in the
# fallback chain). llama-3.3-70b-versatile / llama-3.1-8b-instant are retired.
# All generation goes through ai_gateway (registry -> router -> AIGateway).
# See Documents/AI/model-routing.md and ADR-014 in Documents/architecture-decisions.md.
DEFAULT_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploaded_docs"
CHROMA_DIR = BASE_DIR / "chroma_db"
META_FILE  = BASE_DIR / "doc_metadata.json"
KB_FILE    = BASE_DIR / "data" / "drdo_knowledge_base.json"

# Dataset storage (Data Analyst Agent — VENUAI/agents/data_analyst_agent.py)
# kept separate from UPLOAD_DIR/META_FILE above: a dataset is a different
# kind of object (tabular, analyzed by pandas) from a RAG document (chunked,
# embedded), and conflating the two stores would make both harder to reason
# about. See Documents/AI/data-analyst.md.
DATASET_DIR = BASE_DIR / "uploaded_datasets"
DATASET_META_FILE = BASE_DIR / "datasets.json"

UPLOAD_DIR.mkdir(exist_ok=True)
CHROMA_DIR.mkdir(exist_ok=True)
DATASET_DIR.mkdir(exist_ok=True)


def load_dataset_metadata() -> dict:
    if DATASET_META_FILE.exists():
        with open(DATASET_META_FILE, "r") as f:
            return json.load(f)
    return {}


def save_dataset_metadata(meta: dict):
    with open(DATASET_META_FILE, "w") as f:
        json.dump(meta, f, indent=2)

def load_prefilled_kb() -> dict:
    if KB_FILE.exists():
        try:
            with open(KB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading prefilled KB: {e}")
    return {}

# ── FastAPI App ────────────────────────────────────────────────────────────────
# Production start-up validation (weak JWT secret, wildcard CORS, debug flags, demo
# seeding...) fails fast here; /docs, /redoc and /openapi.json are off in production
# unless ENABLE_DOCS=1. Headers/CORS/limits/error handlers: VENUAI/http_security.py
# (ADR-S4.1, Documents/Backend/http-security.md).
from VENUAI import config as _config
from VENUAI import http_security

_config.enforce_config()
app = FastAPI(
    title="DRDO VenuAI RAG Server",
    description="Knowledge Base RAG pipeline for DRDO research documents",
    version="1.1.0",
    **_config.docs_kwargs(),
)

# Rate limiting: per-user key (JWT subject) falling back to the trusted-proxy-aware IP.
# Per-route @limiter.limit decorators below stay; http_security adds the global,
# per-IP and daily LLM-cost buckets. See Documents/future-additions.md Track A item 10.
limiter = http_security.build_limiter()
http_security.register_security(app, limiter)

# ── Optional Wave B routers ──────────────────────────────────────────────────
# Streams add endpoints by creating VENUAI/<name>.py exposing `router`
# (an APIRouter); no further edits to this file are needed. Absent modules
# are skipped; a module that exists but fails to import is logged loudly.
import importlib as _importlib

for _mod in ("research_api", "voice_api", "memory_api", "trace_api",
             "a2a_api", "approvals_api", "export_api"):
    _full = f"VENUAI.{_mod}"
    try:
        app.include_router(_importlib.import_module(_full).router)
    except ModuleNotFoundError as _e:
        if _e.name == _full:
            logger.debug("optional router %s not present", _mod)
        else:
            logger.warning("optional router %s failed to import: %s", _mod, _e)
    except ImportError as _e:
        logger.warning("optional router %s failed to import: %s", _mod, _e)

def _start_model_warmup() -> None:
    """Load the NLI verifier + reranker in a background thread so the first request does not
    pay the (tens of seconds) model-load cost. Skipped under pytest and WARMUP_MODELS=off."""
    if os.getenv("WARMUP_MODELS", "on").strip().lower() in ("off", "0", "false") or "pytest" in sys.modules:
        return
    import threading
    from VENUAI.answer_pipeline import warm_models
    threading.Thread(target=warm_models, daemon=True, name="model-warmup").start()


@app.on_event("startup")
def _on_startup() -> None:
    _start_model_warmup()


# ── Lazy-loaded singletons (initialized on first use) ─────────────────────────
_embeddings = None
_vector_store = None
_llm = None

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def get_embedding_model_name() -> str:
    """Env EMBEDDING_MODEL; default keeps the existing chroma_db valid.
    Changing it requires re-indexing - see Documents/AI/retrieval.md."""
    return os.getenv("EMBEDDING_MODEL", "").strip() or DEFAULT_EMBEDDING_MODEL


def get_embeddings():
    global _embeddings
    if _embeddings is None:
        name = get_embedding_model_name()
        logger.info(f"Loading embedding model ({name})...")
        try:   # stream B2: env-selectable embedding model (default MiniLM)
            from VENUAI.embeddings_config import get_embeddings as _cfg_embeddings
            _embeddings = _cfg_embeddings()
        except ImportError:
            from langchain_community.embeddings import HuggingFaceEmbeddings
            _embeddings = HuggingFaceEmbeddings(
                model_name=name,
                model_kwargs={"device": "cpu"},
                encode_kwargs={"normalize_embeddings": True},
            )
        logger.info("Embedding model loaded")
    return _embeddings


def get_vector_store():
    global _vector_store
    if _vector_store is None:
        from langchain_community.vectorstores import Chroma
        _vector_store = Chroma(
            collection_name="drdo_knowledge_base",
            embedding_function=get_embeddings(),
            persist_directory=str(CHROMA_DIR),
        )
        _check_embedding_model(_vector_store)
    return _vector_store

class _GatewayLLM:
    """Minimal `.invoke(prompt)` adapter over AIGateway.complete(GENERATION), so
    the LangGraph nodes share the gateway's fallback chain, circuit breaker,
    timeouts, reasoning-effort handling and telemetry. The result carries
    `.model_id` = the model that actually answered."""

    def invoke(self, prompt_text: str, prefer_model: Optional[str] = None, effort: Optional[str] = None):
        from types import SimpleNamespace
        from VENUAI.ai_gateway.gateway import get_gateway
        from VENUAI.ai_gateway.model_router import TaskType
        result = get_gateway().complete(
            task_type=TaskType.GENERATION,
            messages=[{"role": "user", "content": prompt_text}],
            max_tokens=1500,
            temperature=0.2,
            prefer_model=prefer_model,
            reasoning_effort=effort,
        )
        return SimpleNamespace(content=result.content, model_id=result.model_id)


def _check_embedding_model(vs) -> None:
    """
    Record the embedding model in the collection metadata (only on an empty
    collection - existing data is never touched) and warn loudly when the
    configured model differs from the one the collection was built with.
    Legacy collections without the key were built with the default model.
    """
    name = get_embedding_model_name()
    try:
        col = vs._collection
        stored = (col.metadata or {}).get("embedding_model")
        if stored is None:
            if col.count() == 0:
                col.modify(metadata={**(col.metadata or {}), "embedding_model": name})
            elif name != DEFAULT_EMBEDDING_MODEL:
                stored = DEFAULT_EMBEDDING_MODEL  # legacy collection
        if stored is not None and stored != name:
            logger.warning(
                f"EMBEDDING MODEL MISMATCH: collection was built with '{stored}' but EMBEDDING_MODEL "
                f"is '{name}'. Retrieval quality will be garbage until you re-index "
                "(see Documents/AI/retrieval.md)."
            )
    except Exception as err:
        logger.warning(f"Could not verify embedding model against collection metadata: {err}")


def get_llm():
    global _llm
    if _llm is None:
        _llm = _GatewayLLM()
    return _llm


def _primary_model_label() -> str:
    from VENUAI.ai_gateway.model_router import TaskType, route
    chain = route(TaskType.GENERATION)
    return chain[0].model_id if chain else DEFAULT_MODEL


# ── DRDO-Specific RAG Prompt ───────────────────────────────────────────────────
DRDO_RAG_PROMPT_TEMPLATE = """You are VenuAI, an intelligent scientific research assistant for DRDO \
(Defence Research and Development Organisation), India's premier defence R&D agency.

Answer questions accurately based on the document context and conversation history below.
If the answer cannot be found in the context, clearly state:
"This information is not available in the uploaded DRDO documents."

FORMATTING RULES:
1. Start with a clear heading (`### ...`).
2. Present key technical specs, parameters, or lab details in a clean Markdown table when applicable.
3. Use bullet points (`- `) for key highlights.
4. Highlight important lab codes, weapon names, and parameters in **bold**.

--- CHAT HISTORY ---
{chat_history}
--- END CHAT HISTORY ---

--- DOCUMENT CONTEXT ---
{context}
--- END CONTEXT ---

Question: {question}

Answer (technical, formatted with headings, tables, bullet points, and source citations):"""

# ── Document Metadata Store ────────────────────────────────────────────────────
def load_metadata() -> dict:
    if META_FILE.exists():
        with open(META_FILE, "r") as f:
            return json.load(f)
    return {}


def save_metadata(meta: dict):
    with open(META_FILE, "w") as f:
        json.dump(meta, f, indent=2)


# ── Pydantic Models ────────────────────────────────────────────────────────────
class QueryRequest(BaseModel):
    question: str
    top_k: int = 4
    history: Optional[List[dict]] = None
    # New fields — all have defaults for backward compatibility
    session_id: str = "default-session"
    user_id: Optional[str] = None
    agent_mode: bool = False        # ReAct agentic loop
    multi_agent_mode: bool = False  # specialist multi-agent routing
    # Optional scope: intersected with the caller's authorised doc ids (never widens access).
    doc_ids: Optional[List[str]] = None
    # "fast" skips the corrective loop; "standard" (default) / "deep" run it.
    depth: str = "standard"


class QuerySource(BaseModel):
    document: str
    page: int
    chunk_preview: str


class QueryResponse(BaseModel):
    answer: str
    sources: List[QuerySource]
    model: str
    chunks_retrieved: int
    # New verification & quality fields
    # None = not measured (never a made-up number); see verified / verification_reason.
    grounding_score: Optional[float] = None
    hallucination_score: Optional[float] = None
    verification_flags: List[str] = []
    annotated_answer: Optional[str] = None
    # Claim-level evidence verification fields (None = not measured).
    evidence_coverage: Optional[float] = None
    unsupported_claim_count: Optional[int] = None
    contradicted_claim_count: Optional[int] = None
    critique_passed: bool = False
    # Trust & safety (additive)
    blocked: bool = False
    block_reason: Optional[str] = None
    verified: bool = False
    verification_reason: Optional[str] = None
    citations: List[dict] = []
    safety_check: Optional[str] = None          # passed | flagged | unavailable | disabled
    answer_basis: Optional[str] = None          # uploaded_documents | builtin_knowledge_base | none
    answer_basis_notice: Optional[str] = None
    quarantined_chunks: int = 0
    trace_id: Optional[str] = None
    # Pipeline fields (VENUAI/run_turn.py). None / [] = not produced for this mode, never invented.
    retrieved_chunks: List[dict] = []
    usage: Optional[dict] = None            # provider-reported tokens; null fields when unknown
    ttft_ms: Optional[int] = None
    latency_ms: Optional[int] = None
    cache_hit: bool = False
    model_path: List[str] = []              # models that produced answer text, in order (cascade)
    followups: List[str] = []
    contradictions: List[dict] = []
    corrective: Optional[dict] = None       # {iterations, actions}
    memories_used: List[dict] = []
    pending_approval: Optional[dict] = None


class DocumentInfo(BaseModel):
    id: str
    filename: str
    pages: int
    chunks: int
    upload_time: str
    size_kb: float
    tags: Optional[dict] = None


# ── Auth Routes ────────────────────────────────────────────────────────────────
# Real, server-side authentication. Replaces the previous frontend-only mock
# (chat-app/src/lib/mockAuth.ts) — see Documents/Backend/authentication.md.

@app.post("/auth/register", response_model=auth.UserOut, status_code=201)
@limiter.limit("5/minute")
def register(request: Request, body: auth.RegisterRequest):
    return auth.create_user(
        body.email, body.password,
        tenant_id=body.tenant_id or "default",
        organization_name=body.organization_name,
    )


@app.post("/auth/login", response_model=auth.TokenResponse)
@limiter.limit("10/minute")
def login(request: Request, body: auth.LoginRequest):
    user_row = auth.get_user_by_email(body.email)
    if user_row is None or not auth.verify_password(body.password, user_row["hashed_password"]):
        if user_row is not None:
            auth.record_failed_login(body.email)
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    if auth.is_locked(user_row):
        raise HTTPException(
            status_code=423,
            detail="Account temporarily locked due to repeated failed login attempts. Try again later.",
        )
    auth.reset_failed_login(body.email)
    access = auth.create_access_token(user_row["id"], user_row["role"])
    refresh = auth.issue_refresh_token(user_row["id"])
    return auth.TokenResponse(
        access_token=access,
        refresh_token=refresh,
        user=auth.UserOut(id=user_row["id"], email=user_row["email"], role=user_row["role"], tenant_id=user_row["tenant_id"]),
    )


@app.post("/auth/refresh", response_model=auth.TokenResponse)
@limiter.limit("20/minute")
def refresh_token(request: Request, body: auth.RefreshRequest):
    new_access, new_refresh = auth.rotate_refresh_token(body.refresh_token)
    payload = auth.decode_access_token(new_access)
    user_row = auth.get_user_by_id(payload["sub"])
    return auth.TokenResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        user=auth.UserOut(id=user_row["id"], email=user_row["email"], role=user_row["role"], tenant_id=user_row["tenant_id"]),
    )


@app.post("/auth/logout", status_code=204)
def logout(body: auth.RefreshRequest):
    auth.revoke_refresh_token(body.refresh_token)
    return None


# ── OAuth (Google / GitHub) — Phase 5 ────────────────────────────────────────
# Real authorization-code-flow implementation (VENUAI/oauth.py) — genuinely
# blocked for live use here only by missing real provider credentials, see
# Documents/Backend/oauth.md. The Next.js frontend, not this backend, is
# responsible for the browser redirect and for setting httpOnly cookies
# from the tokens this returns — same division of responsibility as
# /auth/login.

class OAuthAuthorizeResponse(BaseModel):
    authorize_url: str


@app.get("/auth/oauth/{provider}/login", response_model=OAuthAuthorizeResponse)
def oauth_login(provider: str, redirect_uri: str):
    """Returns the provider's real authorization URL for the frontend to
    redirect the browser to. 503 if the provider isn't configured —
    never a fabricated URL that can't actually complete."""
    from VENUAI.oauth import OAuthNotConfiguredError, build_authorize_url

    try:
        return OAuthAuthorizeResponse(authorize_url=build_authorize_url(provider, redirect_uri))
    except OAuthNotConfiguredError as e:
        raise http_security.safe_error(e, "This sign-in provider is not configured.", 503)
    except ValueError as e:
        raise http_security.safe_error(e, "Invalid sign-in request.", 400)


class OAuthCallbackRequest(BaseModel):
    code: str
    state: str
    redirect_uri: str


@app.post("/auth/oauth/{provider}/callback", response_model=auth.TokenResponse)
@limiter.limit("10/minute")
def oauth_callback(request: Request, provider: str, body: OAuthCallbackRequest):
    """
    Exchanges a real authorization code for a verified user identity,
    then finds-or-creates a local account by email and issues the same
    real JWT access/refresh token pair /auth/login does — an OAuth sign-in
    is just a different way of establishing identity, not a different
    session mechanism.
    """
    import secrets
    from VENUAI.oauth import OAuthExchangeError, OAuthNotConfiguredError, exchange_code_for_user, verify_state

    if not verify_state(body.state, expected_provider=provider):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state — possible CSRF, please try signing in again.")

    try:
        profile = exchange_code_for_user(provider, body.code, body.redirect_uri)
    except OAuthNotConfiguredError as e:
        raise http_security.safe_error(e, "This sign-in provider is not configured.", 503)
    except OAuthExchangeError as e:
        raise http_security.safe_error(e, "Sign-in with the provider failed.", 502)
    except ValueError as e:
        raise http_security.safe_error(e, "Invalid sign-in request.", 400)

    user_row = auth.get_user_by_email(profile["email"])
    if user_row is None:
        created = auth.create_user(profile["email"], secrets.token_urlsafe(32))
        user_row = auth.get_user_by_email(created.email)

    access_token = auth.create_access_token(user_row["id"], user_row["role"])
    refresh_token = auth.issue_refresh_token(user_row["id"])
    return auth.TokenResponse(
        access_token=access_token, refresh_token=refresh_token,
        user=auth.UserOut(id=user_row["id"], email=user_row["email"], role=user_row["role"], tenant_id=user_row["tenant_id"]),
    )


# ── Password reset (Phase 5) ─────────────────────────────────────────────────
# Real SMTP delivery (VENUAI/email_service.py) — genuinely blocked for live
# email delivery here only by missing real SMTP credentials, see
# Documents/Backend/password-reset.md.

@app.post("/auth/forgot-password", status_code=202)
@limiter.limit("5/minute")
def forgot_password(request: Request, body: auth.ForgotPasswordRequest):
    """
    Always returns 202 regardless of whether the email belongs to a real
    account — never confirms/denies account existence to the caller
    (prevents email enumeration). If SMTP isn't configured, that's a real
    server-side problem (503), not something to hide behind a fake
    "sent" response — but it's still only raised *after* checking the
    account exists, so an unconfigured-SMTP response itself doesn't leak
    account existence either: both outcomes for a real account converge
    on either "sent" or a 503 that reveals nothing about the account.
    """
    from VENUAI import email_service

    user_row = auth.get_user_by_email(body.email)
    if user_row is None:
        return {"status": "accepted"}

    if not email_service.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Password reset email delivery is not configured on this server. Contact an administrator.",
        )

    reset_token = auth.create_password_reset_token(user_row["id"])
    reset_link = f"{os.getenv('FRONTEND_URL', 'http://localhost:3000')}/reset-password?token={reset_token}"
    try:
        email_service.send_email(
            to=body.email,
            subject="Reset your VenuAI password",
            body_text=(
                f"Someone requested a password reset for your VenuAI account.\n\n"
                f"Reset your password: {reset_link}\n\n"
                f"This link expires in 30 minutes. If you didn't request this, ignore this email."
            ),
        )
    except email_service.EmailSendError as e:
        logger.error(f"[forgot_password] Real SMTP send failed: {e}")
        raise HTTPException(status_code=502, detail="Failed to send the password reset email. Please try again later.")

    return {"status": "accepted"}


@app.post("/auth/reset-password", status_code=200)
@limiter.limit("10/minute")
def reset_password(request: Request, body: auth.ResetPasswordRequest):
    """Consumes the signed reset token exactly once in effect — not
    literally single-use (no DB row to mark spent), but changing the
    password revokes every refresh token, and the old password hash
    (which the token doesn't encode) is gone, so replaying the same
    token again just resets to whatever `new_password` is passed this
    time, by the same legitimate caller who already proved email
    ownership by receiving the link."""
    user_id = auth.verify_password_reset_token(body.token)
    if user_id is None:
        raise HTTPException(status_code=400, detail="This password reset link is invalid or has expired.")

    user_row = auth.get_user_by_id(user_id)
    if user_row is None:
        raise HTTPException(status_code=400, detail="This password reset link is invalid or has expired.")

    auth.update_password(user_id, body.new_password)
    return {"status": "password_reset"}


@app.get("/auth/me", response_model=auth.UserOut)
def me(current_user: AuthenticatedUser = Depends(get_current_user)):
    return auth.UserOut(id=current_user.id, email=current_user.email, role=current_user.role, tenant_id=current_user.tenant_id)


def _tenant_out_from_row(row) -> auth.TenantOut:
    return auth.TenantOut(
        tenant_id=row["tenant_id"], name=row["name"], max_users=row["max_users"],
        max_documents=row["max_documents"], max_datasets=row["max_datasets"],
    )


@app.get("/auth/tenant", response_model=auth.TenantOut)
def my_tenant(current_user: AuthenticatedUser = Depends(get_current_user)):
    """The caller's own organization/workspace — see Documents/Backend/multi-tenancy.md."""
    tenant_row = auth.get_tenant(current_user.tenant_id)
    if tenant_row is None:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    return _tenant_out_from_row(tenant_row)


def _count_tenant_documents(tenant_id: str) -> int:
    meta = load_metadata()
    return sum(1 for info in meta.values() if info.get("tenant_id", "default") == tenant_id)


def _count_tenant_datasets(tenant_id: str) -> int:
    meta = load_dataset_metadata()
    return sum(1 for info in meta.values() if info.get("tenant_id", "default") == tenant_id)


def _enforce_document_quota(current_user: AuthenticatedUser) -> None:
    """Shared by /upload and /upload/multimodal — both add a row to the
    same doc_metadata.json store. See Documents/Backend/multi-tenancy.md
    "Quotas"."""
    tenant_row = auth.get_tenant(current_user.tenant_id)
    if tenant_row is None:
        return  # defensive — should never happen for a real authenticated user
    max_documents = tenant_row["max_documents"]
    if _count_tenant_documents(current_user.tenant_id) >= max_documents:
        raise HTTPException(
            status_code=403,
            detail=f"Your organization has reached its document limit ({max_documents}). "
                   "An admin can raise this limit from the Research Workspace.",
        )


def _enforce_dataset_quota(current_user: AuthenticatedUser) -> None:
    tenant_row = auth.get_tenant(current_user.tenant_id)
    if tenant_row is None:
        return
    max_datasets = tenant_row["max_datasets"]
    if _count_tenant_datasets(current_user.tenant_id) >= max_datasets:
        raise HTTPException(
            status_code=403,
            detail=f"Your organization has reached its dataset limit ({max_datasets}). "
                   "An admin can raise this limit from the Research Workspace.",
        )


@app.get("/tenants/usage", response_model=auth.TenantUsageOut)
def tenant_usage(current_user: AuthenticatedUser = Depends(get_current_user)):
    """
    Current member/document/dataset counts against this tenant's quotas —
    visible to any member of the tenant, not just admins, since it's
    aggregate counts, not per-user data. Backs the Research Workspace's
    tenant management panel. See Documents/Backend/multi-tenancy.md "Quotas".
    """
    tenant_row = auth.get_tenant(current_user.tenant_id)
    if tenant_row is None:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    return auth.TenantUsageOut(
        tenant_id=tenant_row["tenant_id"],
        name=tenant_row["name"],
        user_count=auth.get_tenant_user_count(current_user.tenant_id),
        max_users=tenant_row["max_users"],
        document_count=_count_tenant_documents(current_user.tenant_id),
        max_documents=tenant_row["max_documents"],
        dataset_count=_count_tenant_datasets(current_user.tenant_id),
        max_datasets=tenant_row["max_datasets"],
    )


@app.patch("/tenants", response_model=auth.TenantOut)
def update_tenant_settings(
    body: auth.UpdateTenantRequest, current_user: AuthenticatedUser = Depends(require_admin),
):
    """Rename the tenant and/or adjust its quotas — admin-only, scoped to
    the caller's own tenant (there is no endpoint that can target a
    different tenant_id; admin is per-tenant, not a platform operator —
    see Documents/Backend/multi-tenancy.md Design Principle 2)."""
    updated = auth.update_tenant(
        current_user.tenant_id, name=body.name, max_users=body.max_users,
        max_documents=body.max_documents, max_datasets=body.max_datasets,
    )
    logger.info(f"[Tenant] {current_user.tenant_id} settings updated by admin {current_user.id}")
    return updated


@app.get("/tenants/members", response_model=List[auth.UserOut])
def list_tenant_members(current_user: AuthenticatedUser = Depends(require_admin)):
    """List every member of the caller's own tenant — admin-only."""
    return auth.list_tenant_members(current_user.tenant_id)


@app.post("/tenants/members", response_model=auth.UserOut, status_code=201)
@limiter.limit("10/minute")
def add_tenant_member(
    request: Request, body: auth.AddTenantMemberRequest,
    current_user: AuthenticatedUser = Depends(require_admin),
):
    """
    Add a new member directly to the admin's own tenant — this is a real,
    deliberate simplification of a production "invite" flow (no email
    system exists in this project — see Documents/Backend/authentication.md
    Future Improvements — so there is no invite link to send); the admin
    provisions the account and shares the password out-of-band themselves,
    the same way `seed_demo_users()` provisions the two demo accounts.
    Enforces the tenant's max_users quota — see auth.create_user().
    """
    return auth.create_user(body.email, body.password, role=body.role, tenant_id=current_user.tenant_id)


# ── Routes ─────────────────────────────────────────────────────────────────────

def _llm_health() -> dict:
    from VENUAI.ai_gateway.model_router import TaskType, route
    chain = [m.model_id for m in route(TaskType.GENERATION)]
    return {
        "provider": "groq",
        "primary_model": chain[0] if chain else None,
        "fallback_chain": chain[1:],
    }


def _deep_health() -> dict:
    """Dependency checks that never load a model and never expose secrets."""
    out: dict = {}
    try:
        if _vector_store is not None:
            out["chroma"] = {"ok": True, "chunks": int(_vector_store._collection.count())}
        else:
            import chromadb
            hb = chromadb.PersistentClient(path=str(CHROMA_DIR)).heartbeat()
            out["chroma"] = {"ok": bool(hb), "loaded": False}
    except Exception as e:  # noqa: BLE001
        out["chroma"] = {"ok": False, "error": type(e).__name__}
    out["groq_key_present"] = bool(os.getenv("GROQ_API_KEY"))
    try:
        from VENUAI.hallucination_detector import get_hallucination_detector
        out["nli_model_loaded"] = get_hallucination_detector()._nli_model is not None
    except Exception:  # noqa: BLE001
        out["nli_model_loaded"] = False
    try:
        from VENUAI import reranker
        out["reranker_loaded"] = bool(getattr(reranker, "_model", None) or getattr(reranker, "_reranker", None))
    except Exception:  # noqa: BLE001
        out["reranker_loaded"] = False
    try:
        from VENUAI.ai_gateway.gateway import get_gateway
        out["circuit_breakers"] = get_gateway().breaker_states()
    except Exception:  # noqa: BLE001
        out["circuit_breakers"] = {}
    from VENUAI import semantic_cache, model_cascade
    out["answer_cache"] = semantic_cache.mode()
    out["model_cascade"] = model_cascade.enabled()
    out["status"] = "ok" if out["chroma"].get("ok") and out["groq_key_present"] else "degraded"
    return out


@app.get("/health")
def health_check():
    meta = load_metadata()
    deep = _deep_health()
    return {
        "status": deep.pop("status"),
        "service": "DRDO VenuAI RAG Server",
        "version": "1.1.0",
        "documents_ingested": len(meta),
        "vector_store": "ChromaDB (local)",
        "embedding_model": get_embedding_model_name(),
        "llm": _llm_health(),
        "dependencies": deep,
        "note": "Embedding model loads on first upload/query",
    }


@app.post("/upload", response_model=DocumentInfo)
@limiter.limit("10/minute")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Accepts a PDF file, parses it, chunks it, embeds it, and stores
    it in the local ChromaDB vector database. Requires authentication;
    the uploading user becomes the document's owner.
    """
    from langchain_community.document_loaders import PyPDFLoader
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    _enforce_document_quota(current_user)

    # Validates size, extension, and actual file content (magic bytes) —
    # never trusts the client-supplied filename/extension alone.
    content = await validate_upload(file)

    doc_id = str(uuid.uuid4())
    safe_name = generate_storage_name(file.filename)
    save_path = UPLOAD_DIR / f"{doc_id}_{safe_name}"

    with open(save_path, "wb") as f:
        f.write(content)

    file_size_kb = round(len(content) / 1024, 1)
    logger.info(f"Saved uploaded file: {save_path} ({file_size_kb} KB) by user {current_user.id}")

    # Parse PDF
    try:
        loader = PyPDFLoader(str(save_path))
        raw_pages = loader.load()
    except Exception as e:
        save_path.unlink(missing_ok=True)
        raise http_security.safe_error(e, "Failed to parse PDF.", 422)

    num_pages = len(raw_pages)

    # Chunk text
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=120,
        separators=["\n\n", "\n", ".", " "],
    )
    chunks = splitter.split_documents(raw_pages)

    # Attach metadata to each chunk
    for chunk in chunks:
        chunk.metadata["doc_id"]      = doc_id
        chunk.metadata["filename"]    = file.filename
        chunk.metadata["file_size_kb"] = file_size_kb
        chunk.metadata["owner_id"]    = current_user.id
        chunk.metadata["tenant_id"]   = current_user.tenant_id

    # Indirect-injection defence: sanitise + scan chunks, store injection_risk in metadata
    from VENUAI.doc_sanitizer import sanitize_and_scan_chunks; sanitize_and_scan_chunks(chunks)

    # ── Document Intelligence: auto-tag the document ─────────────────────────────
    from VENUAI.doc_intelligence import DocumentIntelligence
    full_text = " ".join(c.page_content for c in chunks[:50])  # first 50 chunks
    doc_tags = DocumentIntelligence().analyze(full_text, file.filename)
    tags_dict = doc_tags.to_dict()
    # Enrich each chunk's metadata with domain tags
    for chunk in chunks:
        chunk.metadata["domain_tags"]    = ",".join(doc_tags.domain_tags)
        chunk.metadata["drdo_labs"]      = ",".join(doc_tags.drdo_labs)
        chunk.metadata["classification"] = doc_tags.classification_level

    # ── Ingest finalisation point: chunks are final text here. Any chunk
    # sanitiser/filter must run immediately BEFORE the contextualisation
    # below, so prefixes and embeddings are built from the sanitised text. ──

    # Contextual chunking (VENUAI/contextual_chunking.py): FAST-model context
    # prefix per chunk, embedded + BM25-indexed but never displayed.
    from starlette.concurrency import run_in_threadpool
    from VENUAI.contextual_chunking import add_context_prefixes
    await run_in_threadpool(add_context_prefixes, chunks, raw_pages, file.filename)

    # Embed + store (embedding model loads here on first call)
    from VENUAI.retrieval import index_chunks
    vs = get_vector_store()
    index_chunks(vs, chunks)   # also invalidates the BM25 cache
    vs.persist()

    num_chunks = len(chunks)
    logger.info(f"Stored {num_chunks} chunks for doc {doc_id} ({file.filename})")
    if os.getenv("RAPTOR", "off").strip().lower() in ("on", "1", "true"):
        try:   # stream B2 seam; costs LLM calls, so opt-in and best-effort
            from VENUAI import raptor
            await run_in_threadpool(raptor.build_summaries, doc_id, current_user.tenant_id, chunks)
        except ImportError:
            logger.debug("raptor module not present")
        except Exception as err:  # noqa: BLE001
            logger.warning(f"[RAPTOR] summary build skipped for {doc_id}: {err}")

    from datetime import datetime
    meta = load_metadata()
    meta[doc_id] = {
        "id": doc_id,
        "filename": file.filename,
        "safe_filename": safe_name,
        "pages": num_pages,
        "chunks": num_chunks,
        "upload_time": datetime.now().isoformat(),
        "size_kb": file_size_kb,
        "file_path": str(save_path),
        "tags": tags_dict,
        "owner_id": current_user.id,
        "tenant_id": current_user.tenant_id,
        "classification": tags_dict.get("classification_level", "UNKNOWN"),
    }
    save_metadata(meta)

    # Automatic Knowledge Graph population (Phase 4). Deterministic
    # extraction only (regex-based, no LLM call) so every /upload stays
    # fast and free — richer LLM-based extraction remains the explicit
    # opt-in via POST /graph/extract (use_llm=true) or /research's
    # extract_graph flag, where the extra latency/cost is something the
    # caller chose. Best-effort: a graph-extraction failure must never
    # fail the document upload itself. See Documents/AI/knowledge-graph.md
    # "Automatic Population".
    try:
        from VENUAI.evidence import Evidence, EvidenceSourceType
        from VENUAI.knowledge_graph.extraction import extract_deterministic
        from VENUAI.knowledge_graph.store import get_graph_store

        doc_evidence = Evidence(
            evidence_id=f"upload-{doc_id}", document_id=doc_id, document_name=file.filename,
            page_number=1, content=full_text, source_type=EvidenceSourceType.UPLOADED_DOCUMENT,
            owner_id=current_user.id, classification=tags_dict.get("classification_level", "UNKNOWN"),
        )
        det_result = extract_deterministic(
            doc_evidence, owner_id=current_user.id,
            classification=tags_dict.get("classification_level", "UNKNOWN"),
            tenant_id=current_user.tenant_id,
        )
        graph_store = get_graph_store()
        for entity in det_result.entities:
            graph_store.add_entity(entity)
        for rel in det_result.relationships:
            graph_store.add_relationship(rel)
        if det_result.entities:
            logger.info(
                f"[Graph] Auto-populated {len(det_result.entities)} entities, "
                f"{len(det_result.relationships)} relationships from upload {doc_id}"
            )
    except Exception as err:
        logger.warning(f"[Graph] Auto-population skipped for upload {doc_id}: {err}")

    return DocumentInfo(
        id=doc_id,
        filename=file.filename,
        pages=num_pages,
        chunks=num_chunks,
        upload_time=meta[doc_id]["upload_time"],
        size_kb=file_size_kb,
        tags=tags_dict,
    )


# ── LangGraph Workflow Engine ────────────────────────────────────────────────────────
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langchain_core.documents import Document


class RAGGraphState(TypedDict):
    question: str
    top_k: int
    history: Optional[List[dict]]
    documents: List[Document]
    answer: str
    sources: List[QuerySource]
    model_used: str
    # Memory fields (Tier 1 & 3)
    working_memory: List[dict]
    semantic_memories: List[str]
    # Verification fields
    grounding_score: float
    hallucination_score: float
    verification_flags: List[str]
    annotated_answer: str
    # Authorization: doc_ids this caller may retrieve from. None = unrestricted
    # (admin). See Documents/Backend/authentication.md.
    allowed_doc_ids: Optional[List[str]]
    # Claim-level evidence verification (VENUAI/verification_pipeline.py) —
    # additive to grounding_score/hallucination_score above, not a replacement.
    evidence_coverage: float
    unsupported_claim_count: int
    contradicted_claim_count: int
    critique_passed: bool
    # Phase 4 — Evidence Explorer (VENUAI/evidence_store.py): the full
    # claim-by-claim breakdown (text, support_status, and the Evidence
    # items backing each claim), serialized to plain dicts here since
    # LangGraph state must stay JSON-shaped. evidence_coverage/
    # unsupported_claim_count above remain the aggregate scores other
    # code already depends on — this is additive detail for the explorer
    # UI, not a replacement.
    evidence_claims: List[dict]
    # Retrieval quality (VENUAI/retrieval.py): the standalone query actually
    # searched (the original question is still what generation answers),
    # per-chunk relevance scores, and the abstention flag.
    retrieval_query: str
    retrieval_scores: List[dict]
    insufficient_context: bool
    # Trust & safety
    user_id: str
    prompt_docs: List[Document]
    quarantined: int
    answer_basis: str
    verified: bool
    verification_reason: Optional[str]
    citations: List[dict]
    safety_check: str
    blocked: bool
    block_reason: Optional[str]
    # Pipeline controls / outputs (VENUAI/run_turn.py)
    tenant_id: str
    depth: str
    want_followups: bool
    model_hint: dict
    feedback: Optional[str]
    extra_queries: List[str]
    corrective_actions: List[str]
    followups: List[str]
    memories: List[dict]


def get_visible_doc_ids(current_user: AuthenticatedUser) -> Optional[List[str]]:
    """
    The set of doc_ids `current_user` is authorized to retrieve chunks from:
    their own uploads plus anything classified PUBLIC, always restricted to
    their own tenant (Phase 4 multi-tenancy — see
    Documents/Backend/multi-tenancy.md). Admins get None only when they have
    no documents outside their own tenant to worry about being excluded from
    is not the behavior we want: admins still only see their own tenant's
    data, since "admin" is a per-tenant role, not a cross-tenant superuser.
    Pre-Phase-4 documents have no "tenant_id" key and are treated as
    belonging to the "default" tenant for backward compatibility.
    """
    meta = load_metadata()
    tenant_docs = {
        doc_id: info for doc_id, info in meta.items()
        if info.get("tenant_id", "default") == current_user.tenant_id
    }
    if current_user.role == "admin":
        return list(tenant_docs.keys())
    return [
        doc_id for doc_id, info in tenant_docs.items()
        if info.get("owner_id") == current_user.id or info.get("classification") == "PUBLIC"
    ]


def retrieve_node(state: RAGGraphState) -> dict:
    """
    History-aware rewrite -> dense + cached BM25 (+ optional KG / RAPTOR sources) -> RRF ->
    cross-encoder rerank -> relevance floor. All of it lives in VENUAI.retrieval (shared
    with agentic_rag / agent_tools). Authorization: allowed_doc_ids filters every stage; an
    empty list retrieves nothing. Retrieval grading: when the best rerank score is weak
    (borderline) the fast model judges relevance (structured) and, if the passages do not
    answer the question, ONE re-retrieval with its better query is made (corrective action
    "re_retrieve", capped, depth != fast).
    """
    from VENUAI.retrieval import retrieve_documents
    from VENUAI import corrective_rag

    question = state["question"]
    top_k = state.get("top_k", 4)
    kw: dict = {}
    if state.get("tenant_id"):
        kw["tenant_id"] = state["tenant_id"]
    if state.get("extra_queries"):
        kw["extra_queries"] = state["extra_queries"]
    history = state.get("history") or state.get("working_memory") or []
    vs = get_vector_store()
    res = retrieve_documents(vs, question, top_k, state.get("allowed_doc_ids"), history=history, **kw)
    actions: List[str] = []
    if (res.documents and not state.get("extra_queries") and corrective_rag.max_iters() > 0
            and corrective_rag.enabled_for(state.get("depth"))
            and corrective_rag.grade_retrieval(res.scores) == "borderline"):
        g = corrective_rag.llm_grade(question, res.documents)
        if g and g.get("relevant") is False and (g.get("better_query") or "").strip():
            kw["extra_queries"] = [g["better_query"].strip()]
            res2 = retrieve_documents(vs, question, top_k, state.get("allowed_doc_ids"), history=history, **kw)
            if res2.documents:
                res, actions = res2, ["re_retrieve"]
    return {
        "documents": res.documents,
        "retrieval_query": res.retrieval_query,
        "retrieval_scores": res.scores,
        "insufficient_context": res.insufficient_context,
        "corrective_actions": actions,
    }


ABSTAIN_ANSWER = (
    "I couldn't find this in the uploaded documents. No passage was relevant enough "
    "to answer it reliably - try rephrasing, or upload the relevant document."
)


def abstain_node(state: RAGGraphState) -> dict:
    """Deterministic short-circuit when retrieval found nothing relevant (no LLM call)."""
    return {
        "answer": ABSTAIN_ANSWER,
        "annotated_answer": ABSTAIN_ANSWER,
        "sources": [],
        "documents": [],
        "verification_flags": ["INSUFFICIENT_CONTEXT"],
        "model_used": "retrieval-abstain (no LLM call)",
        "insufficient_context": True,
        "grounding_score": None,
        "hallucination_score": None,
        "verified": False,
        "verification_reason": "insufficient_context",
        "answer_basis": "none",
        "citations": [],
    }


def route_after_retrieve(state: RAGGraphState) -> str:
    return "abstain" if state.get("insufficient_context") else "generate"


def _kb_notice() -> str:
    from VENUAI.answer_pipeline import KB_NOTICE
    return KB_NOTICE


def _kb_fallback_docs(question: Optional[str] = None) -> List[Document]:
    """Built-in KB as clearly-labelled, citable per-entry documents (stream B2's
    kb_documents when present), else the legacy single dump, or [] if no KB is available."""
    try:
        from VENUAI.kb_documents import kb_fallback_documents
        docs = kb_fallback_documents(question or "", k=5)
        if docs:
            return docs
    except ImportError:
        pass
    except Exception as err:  # noqa: BLE001
        logger.warning(f"kb_documents unavailable, using legacy KB dump: {err}")
    from VENUAI.answer_pipeline import kb_document
    kb_data = load_prefilled_kb()
    if not kb_data:
        return []
    return [kb_document(json.dumps(kb_data, indent=2, ensure_ascii=False)[:8000])]


def _sources_from_docs(docs: List[Document]) -> List[QuerySource]:
    seen_sources: set = set()
    sources: List[QuerySource] = []
    for doc in docs:
        m = doc.metadata
        key = (m.get("filename", "Unknown"), m.get("page", 0))
        if key not in seen_sources:
            seen_sources.add(key)
            sources.append(QuerySource(
                document=m.get("filename", "Unknown Document"),
                page=m.get("page", 0) + 1,
                chunk_preview=doc.page_content[:120].strip() + "…",
            ))
    return sources


def _build_generation_prompt(state: dict):
    """Prompt for the generation call (shared by generate_node and the streaming path).
    Returns (prompt_text, prompt_docs, answer_basis, prompt_info). Static instructions come
    first and per-request data last so provider-side prompt caching can reuse the prefix."""
    from VENUAI.context_engineer import ContextBudget, rank_chunks, compress_history, build_drdo_prompt
    from VENUAI.answer_pipeline import ANSWER_BASIS_DOCUMENTS, ANSWER_BASIS_KB, ANSWER_BASIS_NONE

    question = state["question"]
    docs = state.get("documents", [])
    history = state.get("history") or []
    sem_mems = list(state.get("semantic_memories", []) or [])

    ranked_docs = rank_chunks(docs, question)
    answer_basis = ANSWER_BASIS_DOCUMENTS if ranked_docs else ANSWER_BASIS_NONE
    if not ranked_docs:
        ranked_docs = _kb_fallback_docs(question)
        if ranked_docs:
            answer_basis = ANSWER_BASIS_KB

    prompt_info: dict = {}
    prompt_text = build_drdo_prompt(
        question=question,
        ranked_docs=ranked_docs,
        compressed_history=compress_history(history, max_turns=6),
        budget=ContextBudget(),
        semantic_memories=sem_mems,
        info=prompt_info,
        followups=bool(state.get("want_followups")) and answer_basis != ANSWER_BASIS_NONE,
        feedback=state.get("feedback"),
    )
    return prompt_text, prompt_info.get("used_docs", []), answer_basis, prompt_info


def generate_node(state: RAGGraphState) -> dict:
    """
    Generate an answer using Context Engineering for optimal prompt quality.
    The raw answer (still carrying the <citations> / <followups> blocks) is post-processed
    in verify_node. state["model_hint"] ({prefer_model, effort}) is the cascade's choice.
    """
    prompt_text, prompt_docs, answer_basis, prompt_info = _build_generation_prompt(state)

    llm = get_llm()
    hint = state.get("model_hint") or {}
    if hint and isinstance(llm, _GatewayLLM):
        res = llm.invoke(prompt_text, prefer_model=hint.get("prefer_model"), effort=hint.get("effort"))
    else:
        res = llm.invoke(prompt_text)
    answer_text = res.content if hasattr(res, "content") else str(res)
    answered_by = getattr(res, "model_id", None) or _primary_model_label()

    return {
        "answer": answer_text,
        "sources": _sources_from_docs(prompt_docs),
        "documents": prompt_docs,
        "prompt_docs": prompt_docs,
        "quarantined": prompt_info.get("quarantined", 0),
        "answer_basis": answer_basis,
        "model_used": answered_by,
    }


def _postprocess_answer(raw_answer: str, question: str, prompt_docs: List[Document],
                        user_id: str, answer_basis: str, light: bool = False,
                        want_followups: bool = False) -> dict:
    """
    Shared tail for every answer path: split follow-ups + verify citations, verify the answer
    (fail-closed; independent checks run concurrently), then output guardrails + policy
    classifier (its LLM call overlaps the verification). Returns graph-state fields.
    """
    import inspect
    from concurrent.futures import ThreadPoolExecutor
    from VENUAI import answer_pipeline as ap
    from VENUAI import followups as fu
    from VENUAI.grounded_citations import verify_citations

    raw_clean, fups = fu.split_followups(raw_answer)
    clean, quotes = ap.split_citations(raw_clean)
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="post")
    fb_future = verdict_future = None
    try:
        if want_followups and not fups and clean and answer_basis == ap.ANSWER_BASIS_DOCUMENTS:
            fb_future = ap.ctx_submit(pool, fu.fallback_followups, question, clean)
        if "verdict" in inspect.signature(ap.apply_output_safety).parameters:
            from VENUAI import safety_classifiers as sc
            verdict_future = ap.ctx_submit(pool, sc.safeguard_classify, clean)
        ver = ap.run_verification(clean, question, prompt_docs, light=light)
        final_answer = ver.pop("final_answer")
        citations = verify_citations(final_answer, quotes, prompt_docs)
        if verdict_future is not None:
            safe = ap.apply_output_safety(final_answer, ver.get("grounding_score"), user_id=user_id,
                                          verdict=verdict_future.result())
        else:
            safe = ap.apply_output_safety(final_answer, ver.get("grounding_score"), user_id=user_id)
        if fb_future is not None:
            try:
                fups = fb_future.result()
            except Exception:  # noqa: BLE001
                fups = []
    finally:
        pool.shutdown(wait=False)
    if safe["blocked"]:
        ver["verified"] = False
        ver["verification_reason"] = "answer withheld by output safety policy"
        citations = []
        fups = []
    result = {
        **ver,
        "answer": safe["answer"],
        "annotated_answer": safe["answer"] if safe["blocked"] else (ver.get("annotated_answer") or safe["answer"]),
        "citations": citations,
        "safety_check": safe["safety_check"],
        "blocked": safe["blocked"],
        "block_reason": safe["block_reason"],
        "followups": fups,
    }
    if answer_basis == ap.ANSWER_BASIS_KB and not safe["blocked"]:
        result["answer"] = f"_{ap.KB_NOTICE}_\n\n{result['answer']}"
        result["annotated_answer"] = f"_{ap.KB_NOTICE}_\n\n{result['annotated_answer']}"
    return result


def verify_node(state: RAGGraphState) -> dict:
    """
    Strip + verify quote-level citations, run VerifierAgent + HallucinationDetector +
    the claim-level VerificationPipeline, then output guardrails. Fail-closed: a verifier
    error yields verified=False with a reason, never a 500 and never a silent pass.
    """
    return _postprocess_answer(
        state.get("answer", ""), state.get("question", ""),
        state.get("prompt_docs") or state.get("documents", []),
        state.get("user_id") or "user", state.get("answer_basis") or "uploaded_documents",
        want_followups=bool(state.get("want_followups")),
    )


def fallback_kb_node(state: RAGGraphState) -> dict:
    """No uploaded documents at all: answer from the built-in KB, labelled as such, and
    verify against the KB text actually used (no fixed scores)."""
    from VENUAI.context_engineer import ContextBudget, build_drdo_prompt
    from VENUAI.answer_pipeline import ANSWER_BASIS_KB

    kb_docs = _kb_fallback_docs(state.get("question"))
    if not kb_docs:
        raise HTTPException(status_code=404, detail="No documents in knowledge base.")

    prompt_info: dict = {}
    prompt_text = build_drdo_prompt(
        question=state["question"], ranked_docs=kb_docs, compressed_history="",
        budget=ContextBudget(), semantic_memories=state.get("semantic_memories") or [],
        info=prompt_info,
    )
    prompt_docs = prompt_info.get("used_docs", kb_docs)
    llm = get_llm()
    res = llm.invoke(prompt_text)
    ans_text = res.content if hasattr(res, 'content') else str(res)
    answered_by = getattr(res, "model_id", None) or _primary_model_label()
    out = _postprocess_answer(ans_text, state["question"], prompt_docs,
                              state.get("user_id") or "user", ANSWER_BASIS_KB)
    out.update({
        "sources": _sources_from_docs(prompt_docs),
        "documents": prompt_docs,
        "prompt_docs": prompt_docs,
        "quarantined": prompt_info.get("quarantined", 0),
        "answer_basis": ANSWER_BASIS_KB,
        "model_used": answered_by,
    })
    return out


def route_query_node(state: RAGGraphState) -> str:
    meta = load_metadata()
    if meta:
        return "retrieve"
    return "fallback_kb"


# Build & Compile LangGraph State Graph (with verify_node)
workflow = StateGraph(RAGGraphState)
workflow.add_node("retrieve",    retrieve_node)
workflow.add_node("generate",    generate_node)
workflow.add_node("verify",      verify_node)      # NEW
workflow.add_node("fallback_kb", fallback_kb_node)

workflow.add_conditional_edges(
    START,
    route_query_node,
    {"retrieve": "retrieve", "fallback_kb": "fallback_kb"}
)
workflow.add_node("abstain",     abstain_node)
workflow.add_conditional_edges(
    "retrieve", route_after_retrieve, {"generate": "generate", "abstain": "abstain"}
)
workflow.add_edge("abstain",     END)
workflow.add_edge("generate",    "verify")          # NEW: verification after generate
workflow.add_edge("verify",      END)
workflow.add_edge("fallback_kb", END)

venuai_graph = workflow.compile()


_RESPONSE_FIELDS = set(QueryResponse.model_fields)


def _mod():
    return sys.modules[__name__]


def _to_response(result: dict, **overrides) -> QueryResponse:
    """Plain-dict turn result -> QueryResponse (unknown keys dropped, nothing invented)."""
    data = {k: v for k, v in result.items() if k in _RESPONSE_FIELDS}
    if "sources" in data:
        data["sources"] = [s if isinstance(s, QuerySource) else QuerySource(**s) for s in data["sources"]]
    data.setdefault("answer", "No response generated.")
    data.setdefault("model", result.get("model_used") or "LangGraph Workflow")
    data.setdefault("chunks_retrieved", len(result.get("documents") or result.get("retrieved_chunks") or []))
    data["annotated_answer"] = result.get("annotated_answer") or data["answer"]
    if result.get("answer_basis") == "builtin_knowledge_base":
        data["answer_basis_notice"] = _kb_notice()
    data["quarantined_chunks"] = result.get("quarantined", result.get("quarantined_chunks", 0)) or 0
    data.update(overrides)
    return QueryResponse(**data)


def _initial_state(ctx, mem_ctx: dict, current_user) -> dict:
    from VENUAI import followups as _fu
    from VENUAI import model_cascade as _mc
    plan = _mc.plan_generation(ctx.question, downshift=ctx.downshift)
    ctx.plan = plan
    mem_texts = list(mem_ctx.get("semantic", [])) + [m["text"] for m in ctx.memories]
    return {
        "question": ctx.question, "top_k": ctx.top_k, "history": ctx.history, "documents": [],
        "answer": "", "sources": [], "model_used": "",
        "working_memory": mem_ctx.get("working", []), "semantic_memories": mem_texts,
        "grounding_score": None, "hallucination_score": None, "verification_flags": [],
        "annotated_answer": "", "allowed_doc_ids": ctx.allowed_doc_ids,
        "evidence_coverage": None, "unsupported_claim_count": None, "contradicted_claim_count": None,
        "critique_passed": False, "evidence_claims": [], "retrieval_query": ctx.question,
        "retrieval_scores": [], "insufficient_context": False, "user_id": current_user.id,
        "tenant_id": current_user.tenant_id, "depth": ctx.depth,
        "want_followups": _fu.enabled(),
        "model_hint": ({"prefer_model": plan.prefer_model, "effort": plan.effort}
                       if (plan.prefer_model or plan.effort) else {}),
        "feedback": None, "extra_queries": [], "corrective_actions": [], "followups": [],
    }


def _corrective_and_finish(ctx, base: dict, first: dict) -> dict:
    """Shared by /query and /query/stream: capped corrective loop, then the common tail."""
    from VENUAI import run_turn as rt

    def regenerate(prev: dict, new_docs, feedback: List[str], plan) -> dict:
        st = dict(base)
        st["documents"] = new_docs or prev.get("documents") or prev.get("prompt_docs") or []
        st["retrieval_scores"] = prev.get("retrieval_scores", [])
        st["model_hint"] = ({"prefer_model": plan.prefer_model, "effort": plan.effort}
                            if (plan.prefer_model or plan.effort) else {})
        if feedback:
            st["feedback"] = ("These statements in your previous answer are NOT supported by the passages; "
                              "remove them or restate only what the passages say:\n- " + "\n- ".join(feedback[:5]))
        ctx.sink.emit("generating", "start", "Regenerating answer")
        gen = generate_node(st)
        ctx.note_model(gen.get("model_used"))
        ctx.sink.emit("generating", "end", "", model=gen.get("model_used"))
        ctx.sink.emit("verifying", "start", "Verifying answer")
        ver = verify_node({**st, **gen})
        ctx.sink.emit("verifying", "end", "", verified=bool(ver.get("verified")))
        merged = {**prev, **gen, **ver}
        merged["sources"] = gen.get("sources", prev.get("sources", []))
        return merged

    def re_retrieve(feedback: List[str]):
        from VENUAI.retrieval import retrieve_documents
        extra = [f for f in feedback[:2] if f]
        if not extra:
            return None
        ctx.sink.emit("retrieving", "start", "Searching again")
        kw = {"tenant_id": ctx.user.tenant_id}
        res = retrieve_documents(get_vector_store(), ctx.question, ctx.top_k, ctx.allowed_doc_ids,
                                 history=ctx.history, extra_queries=extra, **kw)
        ctx.sink.emit("retrieving", "end", f"Found {len(res.documents)} passages", n=len(res.documents))
        return res.documents or None

    state, cr = rt.corrective_stage(ctx, _mod(), first, regenerate_fn=regenerate, re_retrieve_fn=re_retrieve)
    pre = list(first.get("corrective_actions") or [])      # grader-driven re-retrieval before generation
    if pre:
        cr.actions = pre + [a for a in cr.actions if a not in pre]
        cr.iterations += len(pre)
    return rt.finish_turn(ctx, _mod(), state, cr)


def _guardrail_blocked_response(g_res, trace_id: str) -> QueryResponse:
    return QueryResponse(
        answer=g_res.sanitized_input_or_output, sources=[], model="DRDO Defence Guardrails Policy",
        chunks_retrieved=0, grounding_score=None, hallucination_score=None,
        verification_flags=[f"GUARDRAIL_TRIGGERED: {g_res.violation_type}"],
        annotated_answer=g_res.sanitized_input_or_output, blocked=True,
        block_reason=g_res.block_reason or g_res.violation_type, verified=False,
        verification_reason="request blocked by input guardrails", trace_id=trace_id,
    )


def _agent_extras(ctx, mode_tools: List[str]) -> dict:
    pending = None
    try:
        from VENUAI import approvals
        if hasattr(approvals, "pop_pending"):
            pending = approvals.pop_pending(ctx.user.id)
    except Exception as err:  # noqa: BLE001
        logger.debug(f"approvals.pop_pending unavailable: {err}")
    return {"pending_approval": pending}


def _react_messages_usage(messages) -> tuple:
    """Real token usage reported by the chat model on each AIMessage (None when unreported)."""
    p = c = 0
    seen = False
    for m in messages:
        um = getattr(m, "usage_metadata", None)
        if um:
            seen = True
            p += um.get("input_tokens", 0) or 0
            c += um.get("output_tokens", 0) or 0
    return (p, c) if seen else (None, None)


def _react_run(ctx, question: str, request_tools_builder):
    """ReAct through the gateway's model chain: each model is tried in order (circuit-breaker
    aware); rate limits / provider errors move to the next model, anything else is raised."""
    from langgraph.prebuilt import create_react_agent
    from VENUAI.ai_gateway.gateway import get_gateway
    from VENUAI.ai_gateway.model_router import TaskType as _TT, reasoning_effort_for as _eff, route
    from VENUAI.ai_gateway.model_registry import get_model as _get_model

    gw = get_gateway()
    last: Optional[Exception] = None
    for entry in route(_TT.GENERATION):
        if gw.breaker_open(entry.model_id):
            continue
        style = (_get_model(entry.model_id) or entry).reasoning_style
        llm = get_chat_groq(entry.model_id, temperature=0.2, max_tokens=3000,
                            reasoning_effort=_eff(_TT.GENERATION) if style == "effort" else None)
        collected: List[Document] = []
        tools = request_tools_builder(collected)
        try:
            t0 = time.time()
            result = create_react_agent(llm, tools).invoke(
                {"messages": [{"role": "user", "content": question}]}, config={"recursion_limit": 8})
            gw.breaker_record(entry.model_id, True)
            p, c = _react_messages_usage(result["messages"])
            if ctx.meter is not None:
                ctx.meter.record(entry.model_id, "react", p, c, (time.time() - t0) * 1000)
            ctx.note_model(entry.model_id)
            return result, collected, entry.model_id
        except Exception as e:  # noqa: BLE001
            last = e
            gw.breaker_record(entry.model_id, False)
            logger.warning(f"[react] model {entry.model_id} failed ({type(e).__name__}); trying next in chain")
    raise last or RuntimeError("no model available for the ReAct agent")


@app.post("/query", response_model=QueryResponse)
@limiter.limit("30/minute")
def query_knowledge_base(
    request: Request,
    req: QueryRequest,
    background_tasks: "BackgroundTasks",
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Run a RAG query. Supports standard RAG, ReAct agent, and multi-agent modes. Every mode
    ends in the shared tail of VENUAI/run_turn.py (verification, safety, budgets, cache,
    follow-ups, usage, trace) - see Documents/AI/pipeline.md.
    """
    import time as _time
    from VENUAI import run_turn as rt
    from VENUAI.observability import RequestTrace, new_request_id

    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    # The authenticated user's own ID is the source of truth for identity -
    # req.user_id is client-supplied and must never be trusted.
    req.user_id = current_user.id
    trace = RequestTrace(request_id=new_request_id(), route="/query", user_id=current_user.id)

    from VENUAI.guardrails import get_guardrail_engine
    g_res = get_guardrail_engine().validate_input(req.question, user_id=req.user_id or "anonymous")
    if not g_res.passed:
        trace.log_summary(success=False, extra={"stage": "guardrail_blocked", "violation": g_res.violation_type})
        return _guardrail_blocked_response(g_res, trace.request_id)

    t_start = _time.time()
    agent_used = "rag"
    tools_called: List[str] = []
    error_str = None

    from VENUAI.memory_manager import get_memory_manager
    mm = get_memory_manager()
    mem_ctx = rt._call_mm(mm.get_context, req.session_id, req.user_id or "anonymous", req.question,
                          tenant_id=current_user.tenant_id)
    working_memory = mem_ctx.get("working", [])
    mode = "multi_agent" if req.multi_agent_mode else ("react" if req.agent_mode else "rag")
    try:
        ctx = rt.begin_turn(
            req.question, current_user, session_id=req.session_id, top_k=req.top_k,
            history=req.history or working_memory, visible_doc_ids=get_visible_doc_ids(current_user),
            requested_doc_ids=req.doc_ids, depth=req.depth, mode=mode, trace_id=trace.request_id,
        )
    except rt.BudgetRefused as refused:
        raise HTTPException(status_code=429, detail=str(refused))
    effective_history = ctx.history

    try:
        # Mode: Multi-Agent
        if req.multi_agent_mode:
            import asyncio
            from VENUAI.agents.orchestrator import OrchestratorAgent
            orchestrator = OrchestratorAgent(get_groq_client())
            agent_result = asyncio.run(
                orchestrator.route(req.question, effective_history, mem_ctx,
                                   allowed_doc_ids=ctx.allowed_doc_ids)
            )
            agent_used = "multi_agent"
            ctx.note_model(agent_result.agent_name)
            fin = rt.finish_agent_turn(ctx, _mod(), answer=agent_result.answer,
                                       docs=agent_result.documents, agent_label="multi_agent")
            ma_ver, ma_safe = fin["ver"], fin["safe"]
            evidence_coverage, unsupported_count, contradicted_count, critique_passed = None, None, None, False
            try:
                from VENUAI import evidence_store
                from VENUAI.verification_pipeline import get_verification_pipeline
                pipeline_result = get_verification_pipeline().run(
                    req.question, fin["answer"], agent_result.documents)
                evidence_coverage = pipeline_result.evidence_graph.evidence_coverage
                unsupported_count = pipeline_result.critique.unsupported_claim_count
                contradicted_count = pipeline_result.critique.contradicted_claim_count
                critique_passed = pipeline_result.critique.passed
                background_tasks.add_task(
                    evidence_store.save_record, current_user.id, current_user.tenant_id, req.question,
                    fin["answer"], "multi_agent",
                    evidence_store.serialize_claims(pipeline_result.evidence_graph.claims),
                    evidence_coverage, unsupported_count, contradicted_count, critique_passed)
            except Exception as verify_err:
                logger.warning(f"[multi_agent] VerificationPipeline skipped: {verify_err}")
            trace.log_summary(success=True, extra={"stage": "multi_agent",
                                                   "retrieval_coverage": agent_result.retrieval_coverage})
            t_end = _time.time()
            background_tasks.add_task(rt._call_mm, mm.save_turn, req.session_id, req.user_id or "anonymous",
                                      req.question, fin["answer"], tenant_id=current_user.tenant_id)
            from VENUAI.analytics import get_analytics
            background_tasks.add_task(
                get_analytics().log_query, req.session_id, req.user_id or "anonymous", req.question,
                len(fin["answer"]), (t_end - t_start) * 1000, ma_ver.get("grounding_score") or 0.0,
                ma_ver.get("hallucination_score") or 0.0, agent_used, tools_called, None)
            from VENUAI.answer_pipeline import ANSWER_BASIS_DOCUMENTS, ANSWER_BASIS_NONE
            extras = {**fin["extras"], **_agent_extras(ctx, [])}
            return QueryResponse(
                answer=fin["answer"],
                sources=[QuerySource(document=s.document, page=s.page, chunk_preview=s.chunk_preview)
                         for s in agent_result.sources],
                model=agent_result.agent_name, chunks_retrieved=len(agent_result.sources),
                grounding_score=ma_ver.get("grounding_score"),
                hallucination_score=ma_ver.get("hallucination_score"),
                verification_flags=ma_ver.get("verification_flags", []),
                annotated_answer=fin["answer"], evidence_coverage=evidence_coverage,
                unsupported_claim_count=unsupported_count, contradicted_claim_count=contradicted_count,
                critique_passed=critique_passed, blocked=ma_safe["blocked"], block_reason=ma_safe["block_reason"],
                verified=ma_ver["verified"] and not ma_safe["blocked"],
                verification_reason=ma_ver.get("verification_reason"), safety_check=ma_safe["safety_check"],
                answer_basis=ANSWER_BASIS_DOCUMENTS if agent_result.documents else ANSWER_BASIS_NONE,
                **extras,
            )

        # Mode: ReAct Agent (model chain with fallback; tools bound to this caller)
        if req.agent_mode:
            import inspect as _inspect
            from VENUAI.agent_tools import build_agent_tools

            def _tools(collected: List[Document]):
                kw = dict(user_id=current_user.id, allowed_doc_ids=ctx.allowed_doc_ids,
                          user_role=current_user.role, collected_docs=collected)
                if "tenant_id" in _inspect.signature(build_agent_tools).parameters:
                    kw["tenant_id"] = current_user.tenant_id
                return build_agent_tools(**kw)

            result, collected_docs, react_model = _react_run(ctx, req.question, _tools)
            final_msg = result["messages"][-1]
            answer_text = final_msg.content if hasattr(final_msg, "content") else str(final_msg)
            agent_used = "react_agent"
            react_sources = [QuerySource(**s.model_dump()) for s in _sources_from_docs(collected_docs)]

            fin = rt.finish_agent_turn(ctx, _mod(), answer=answer_text, docs=collected_docs,
                                       agent_label="react_agent")
            react_ver, react_safe = fin["ver"], fin["safe"]
            answer_text = fin["answer"]
            evidence_coverage, unsupported_count, contradicted_count, critique_passed = None, None, None, False
            from VENUAI import evidence_store
            try:
                from VENUAI.verification_pipeline import get_verification_pipeline
                pipeline_result = get_verification_pipeline().run(req.question, answer_text, collected_docs)
                evidence_coverage = pipeline_result.evidence_graph.evidence_coverage
                unsupported_count = pipeline_result.critique.unsupported_claim_count
                contradicted_count = pipeline_result.critique.contradicted_claim_count
                critique_passed = pipeline_result.critique.passed
                background_tasks.add_task(
                    evidence_store.save_record, current_user.id, current_user.tenant_id, req.question,
                    answer_text, "react_agent",
                    evidence_store.serialize_claims(pipeline_result.evidence_graph.claims),
                    evidence_coverage, unsupported_count, contradicted_count, critique_passed)
            except Exception as verify_err:
                logger.warning(f"[react_agent] VerificationPipeline skipped: {verify_err}")

            trace.log_summary(success=True, extra={"stage": "react_agent"})
            for msg in result["messages"]:
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        tools_called.append(tc.get("name", ""))
            t_end = _time.time()
            background_tasks.add_task(rt._call_mm, mm.save_turn, req.session_id, req.user_id or "anonymous",
                                      req.question, answer_text, tenant_id=current_user.tenant_id)
            from VENUAI.analytics import get_analytics
            background_tasks.add_task(
                get_analytics().log_query, req.session_id, req.user_id or "anonymous", req.question,
                len(answer_text), (t_end - t_start) * 1000, react_ver.get("grounding_score") or 0.0,
                react_ver.get("hallucination_score") or 0.0, agent_used, tools_called, None)
            from VENUAI.answer_pipeline import ANSWER_BASIS_DOCUMENTS, ANSWER_BASIS_NONE
            extras = {**fin["extras"], **_agent_extras(ctx, tools_called)}
            return QueryResponse(
                answer=answer_text, sources=react_sources, model=f"{react_model} (ReAct)",
                chunks_retrieved=len(collected_docs), grounding_score=react_ver.get("grounding_score"),
                hallucination_score=react_ver.get("hallucination_score"),
                verification_flags=react_ver.get("verification_flags", []), annotated_answer=answer_text,
                evidence_coverage=evidence_coverage, unsupported_claim_count=unsupported_count,
                contradicted_claim_count=contradicted_count, critique_passed=critique_passed,
                blocked=react_safe["blocked"], block_reason=react_safe["block_reason"],
                verified=react_ver["verified"] and not react_safe["blocked"],
                verification_reason=react_ver.get("verification_reason"), safety_check=react_safe["safety_check"],
                answer_basis=ANSWER_BASIS_DOCUMENTS if collected_docs else ANSWER_BASIS_NONE,
                **extras,
            )

        # Mode: Standard RAG - cache -> LangGraph first pass -> capped corrective loop -> shared tail
        cached = rt.cache_lookup(ctx)
        if cached is not None:
            t_end = _time.time()
            ctx.cache_hit = True
            extras = rt.turn_extras(ctx, [], cached.get("answer", ""), cached.get("followups"), None)
            extras["retrieved_chunks"] = cached.get("retrieved_chunks", [])
            extras["model_path"] = cached.get("model_path_original", [])
            extras["usage"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            extras["corrective"] = cached.get("corrective") or {"iterations": 0, "actions": []}
            extras["memories_used"] = []
            extras["contradictions"] = cached.get("contradictions", [])
            trace.log_summary(success=True, extra={"stage": "cache_hit"})
            return _to_response({**cached, **extras})

        base = _initial_state(ctx, mem_ctx, current_user)
        first = venuai_graph.invoke(dict(base))
        if first.get("model_used") and not str(first["model_used"]).startswith("retrieval-abstain"):
            ctx.note_model(first.get("model_used"))
        result = _corrective_and_finish(ctx, base, first)
        t_end = _time.time()
        final_answer = result.get("answer", "No response generated.")

        background_tasks.add_task(rt._call_mm, mm.save_turn, req.session_id, req.user_id or "anonymous",
                                  req.question, final_answer, tenant_id=current_user.tenant_id)
        from VENUAI.evidence_store import save_record as _save_evidence_record
        background_tasks.add_task(
            _save_evidence_record, current_user.id, current_user.tenant_id, req.question, final_answer, "query",
            result.get("evidence_claims", []), result.get("evidence_coverage") or 0.0,
            result.get("unsupported_claim_count") or 0, result.get("contradicted_claim_count") or 0,
            result.get("critique_passed", False))
        from VENUAI.analytics import get_analytics
        background_tasks.add_task(
            get_analytics().log_query, req.session_id, req.user_id or "anonymous", req.question,
            len(final_answer), (t_end - t_start) * 1000, result.get("grounding_score") or 0.0,
            result.get("hallucination_score") or 0.0, agent_used, tools_called, error_str)
        trace.log_summary(success=True, extra={
            "stage": "standard_rag", "chunks_retrieved": len(result.get("documents", [])),
            "grounding_score": result.get("grounding_score", 0.0),
            "critique_passed": result.get("critique_passed", False),
            "corrective": result.get("corrective")})
        return _to_response(result)

    except HTTPException:
        raise
    except Exception as e:
        # Error hygiene: full detail is logged server-side under a trace id; the client gets a
        # generic message plus that id (never str(e)).
        from VENUAI.ai_gateway.usage import BudgetExceeded
        trace.log_summary(success=False, extra={"stage": "exception", "error_type": type(e).__name__})
        error_str = type(e).__name__
        logger.exception(f"Query execution error [trace_id={trace.request_id}]")
        if isinstance(e, BudgetExceeded):
            raise HTTPException(status_code=429, detail="This request exceeded its token budget "
                                f"(REQUEST_TOKEN_BUDGET). Reference trace ID: {trace.request_id}",
                                headers={"X-Trace-Id": trace.request_id})
        raise HTTPException(
            status_code=500,
            detail=f"Query execution failed. Reference trace ID: {trace.request_id}",
            headers={"X-Trace-Id": trace.request_id},
        )


class ResumeRequest(BaseModel):
    approval_id: str


@app.post("/query/resume")
def resume_query(req: ResumeRequest, current_user: AuthenticatedUser = Depends(get_current_user)):
    """Continue a run that stopped at `pending_approval`. Delegates to approvals.resume
    (stream B3); 503 when the approvals module is not installed."""
    try:
        from VENUAI import approvals
    except ImportError:
        raise HTTPException(status_code=503, detail="Approvals are not available on this deployment.")
    if not hasattr(approvals, "resume"):
        raise HTTPException(status_code=503, detail="Approvals are not available on this deployment.")
    out = approvals.resume(req.approval_id, current_user)
    if hasattr(out, "model_dump"):
        return out.model_dump()
    return out


@app.post("/query/stream")
@limiter.limit("30/minute")
async def query_stream(
    request: Request,
    req: QueryRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Stream a RAG answer via Server-Sent Events. Lines are JSON `data:` events:
      {"step": {id,status,label,t_ms,detail}}   typed pipeline steps (see Documents/AI/pipeline.md)
      {"model": "..."}                          model that started answering
      {"token": "..."}                          visible answer text
      {"done": true, ...}                       final event: answer (replaces streamed text) + every
                                                /query field (citations, scores, usage, followups, ...)
    """
    import asyncio
    import contextvars
    import queue as _queue
    import threading
    from fastapi.responses import StreamingResponse
    from VENUAI import run_turn as rt

    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    req.user_id = current_user.id

    from VENUAI.guardrails import get_guardrail_engine
    from VENUAI.observability import new_request_id
    trace_id = new_request_id()
    g_res = await asyncio.get_running_loop().run_in_executor(
        None, lambda: get_guardrail_engine().validate_input(req.question, user_id=req.user_id or "anonymous")
    )
    if not g_res.passed:
        blocked_event = {
            "blocked": True, "block_reason": g_res.block_reason or g_res.violation_type,
            "violation_type": g_res.violation_type, "answer": g_res.sanitized_input_or_output,
            "done": True, "sources": [], "citations": [], "grounding_score": None,
            "hallucination_score": None, "verified": False,
            "verification_reason": "request blocked by input guardrails", "trace_id": trace_id,
            "retrieved_chunks": [], "usage": None, "ttft_ms": None, "latency_ms": 0, "cache_hit": False,
            "model_path": [], "followups": [], "contradictions": [], "corrective": None,
            "memories_used": [], "pending_approval": None,
        }

        async def blocked_generator():
            yield f"data: {json.dumps(blocked_event)}\n\n"

        return StreamingResponse(
            blocked_generator(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Trace-Id": trace_id},
        )

    events: "_queue.Queue" = _queue.Queue()
    sink = rt.StepSink(events)
    visible_ids = get_visible_doc_ids(current_user)

    def worker():
        from VENUAI import followups as _fu
        from VENUAI.grounded_citations import CitationStreamFilter
        from VENUAI.ai_gateway.gateway import get_gateway
        from VENUAI.ai_gateway.model_router import TaskType as _StreamTT
        try:
            from VENUAI.memory_manager import get_memory_manager
            mm = get_memory_manager()
            mem_ctx = rt._call_mm(mm.get_context, req.session_id, req.user_id or "anonymous", req.question,
                                  tenant_id=current_user.tenant_id)
            try:
                ctx = rt.begin_turn(
                    req.question, current_user, session_id=req.session_id, top_k=req.top_k,
                    history=req.history or mem_ctx.get("working", []), visible_doc_ids=visible_ids,
                    requested_doc_ids=req.doc_ids, depth=req.depth, mode="rag", trace_id=trace_id, sink=sink)
            except rt.BudgetRefused as refused:
                events.put({"error": str(refused), "trace_id": trace_id, "budget_exhausted": True})
                return

            cached = rt.cache_lookup(ctx)
            if cached is not None:
                events.put({"model": "answer-cache"})
                if cached.get("answer"):
                    events.put({"token": cached["answer"]})
                extras = rt.turn_extras(ctx, [], cached.get("answer", ""), cached.get("followups"), None)
                done = {**cached, **extras, "done": True, "cache_hit": True,
                        "retrieved_chunks": cached.get("retrieved_chunks", []),
                        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                        "model_path": cached.get("model_path_original", []), "memories_used": []}
                events.put(_done_event(done))
                return

            base = _initial_state(ctx, mem_ctx, current_user)
            state = dict(base)
            if load_metadata():
                ctx.sink.emit("retrieving", "start", "Searching your documents")
                state.update(retrieve_node(state))
                n = len(state.get("documents", []))
                ctx.sink.emit("retrieving", "end", f"Found {n} passages", n=n,
                              retriever="hybrid", rewritten=state.get("retrieval_query") != req.question)
                if state.get("insufficient_context"):
                    ab = abstain_node(state)
                    first = {**state, **ab}
                    events.put({"model": ab["model_used"]})
                    result = _corrective_and_finish(ctx, base, first)
                    events.put(_done_event(result))
                    return

            ctx.sink.emit("reading", "start", "Reading the most relevant passages")
            prompt_text, prompt_docs, answer_basis, pinfo = _build_generation_prompt(state)
            ctx.sink.emit("reading", "end", f"Using {len(prompt_docs)} passages", n=len(prompt_docs))

            ctx.sink.emit("generating", "start", "Writing the answer")
            cite_filter, fu_filter = CitationStreamFilter(), _fu.FollowupStreamFilter()
            hint = state.get("model_hint") or {}
            gw_stream = get_gateway().stream(
                _StreamTT.GENERATION, messages=[{"role": "user", "content": prompt_text}],
                max_tokens=1500, temperature=0.2,
                prefer_model=hint.get("prefer_model"), reasoning_effort=hint.get("effort"))
            ctx.note_model(gw_stream.model_used)
            if ctx.meter is not None and ctx.meter.ttft_ms is None:
                ctx.meter.ttft_ms = gw_stream.ttft_ms   # measured by the gateway at first token
            events.put({"model": gw_stream.model_used})
            full = ""
            for delta in gw_stream:
                if delta:
                    full += delta
                    vis = cite_filter.feed(fu_filter.feed(delta))
                    if vis:
                        events.put({"token": vis})
            tail = cite_filter.feed(fu_filter.flush()) + cite_filter.flush()
            if tail:
                events.put({"token": tail})
            ctx.sink.emit("generating", "end", "", model=gw_stream.model_used,
                          ttft_ms=int(gw_stream.ttft_ms) if gw_stream.ttft_ms is not None else None)

            ctx.sink.emit("verifying", "start", "Verifying the answer against the sources")
            gen = {"answer": full, "sources": _sources_from_docs(prompt_docs), "documents": prompt_docs,
                   "prompt_docs": prompt_docs, "quarantined": pinfo.get("quarantined", 0),
                   "answer_basis": answer_basis, "model_used": gw_stream.model_used}
            ver = verify_node({**state, **gen})
            ctx.sink.emit("verifying", "end", "", verified=bool(ver.get("verified")))
            first = {**state, **gen, **ver}
            result = _corrective_and_finish(ctx, base, first)
            events.put(_done_event(result))
            rt._call_mm(mm.save_turn, req.session_id, req.user_id or "anonymous", req.question,
                        result.get("answer", ""), tenant_id=current_user.tenant_id)
        except Exception as e:
            # Error hygiene: detail goes to the log under the trace id, not to the client.
            logger.exception(f"[stream] error [trace_id={trace_id}]")
            from VENUAI.ai_gateway.usage import BudgetExceeded
            msg = ("Token budget for this request was exceeded." if isinstance(e, BudgetExceeded)
                   else "Stream failed.")
            events.put({"error": f"{msg} Reference trace ID: {trace_id}", "trace_id": trace_id})
        finally:
            events.put(None)

    ctx_copy = contextvars.copy_context()
    threading.Thread(target=lambda: ctx_copy.run(worker), daemon=True, name="query-stream").start()

    async def event_generator():
        loop = asyncio.get_running_loop()
        while True:
            item = await loop.run_in_executor(None, events.get)
            if item is None:
                break
            yield f"data: {json.dumps(item, default=str)}\n\n"

    return StreamingResponse(
        event_generator(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Trace-Id": trace_id},
    )


_DONE_KEYS = (
    "sources", "citations", "grounding_score", "hallucination_score", "verification_flags", "verified",
    "verification_reason", "evidence_coverage", "unsupported_claim_count", "contradicted_claim_count",
    "critique_passed", "safety_check", "blocked", "block_reason", "answer_basis", "quarantined_chunks",
    "insufficient_context", "retrieved_chunks", "usage", "ttft_ms", "latency_ms", "cache_hit", "model_path",
    "followups", "contradictions", "corrective", "trace_id", "memories_used", "pending_approval",
)


def _done_event(result: dict) -> dict:
    ev = {"done": True, "answer": result.get("answer", "")}
    for k in _DONE_KEYS:
        v = result.get(k)
        if k == "sources":
            v = [s.model_dump() if hasattr(s, "model_dump") else s for s in (v or [])]
        if k == "quarantined_chunks":
            v = result.get("quarantined", v) or 0
        ev[k] = v
    ev["answer_basis_notice"] = _kb_notice() if result.get("answer_basis") == "builtin_knowledge_base" else None
    ev["model"] = result.get("model_used") or result.get("model")
    if ev.get("insufficient_context") is None:
        ev.pop("insufficient_context")
    return ev


class ResearchRequest(BaseModel):
    question: str
    extract_graph: bool = False


class SubquestionResultOut(BaseModel):
    subquestion: str
    answer: str
    evidence_coverage: float


class ResearchResponse(BaseModel):
    research_id: str
    question: str
    plan_is_fallback: bool
    subquestion_results: List[SubquestionResultOut]
    synthesized_answer: str
    evidence_coverage: float
    critique_passed: bool
    truncated: bool
    graph_entities_extracted: int = 0
    graph_relationships_extracted: int = 0
    blocked: bool = False
    block_reason: Optional[str] = None
    safety_check: Optional[str] = None


@app.post("/research", response_model=ResearchResponse)
@limiter.limit("5/minute")
def run_deep_research(
    request: Request,
    req: ResearchRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Multi-step research: VENUAI.agents.deep_research_agent.DeepResearchAgent
    plans sub-questions, runs AgenticRAG per sub-question (authorization-
    filtered), synthesizes, and verifies. Runs synchronously — this is NOT
    yet the async/pausable long-running research job described in
    Documents/future-additions.md; a single call can take up to
    DeepResearchAgent.MAX_RUNTIME_SECONDS (120s) for a multi-subquestion plan.
    """
    import uuid
    from VENUAI.agents.deep_research_agent import DeepResearchAgent
    from VENUAI.memory_manager import get_memory_manager
    from VENUAI.guardrails import get_guardrail_engine

    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    ge = get_guardrail_engine()
    g_res = ge.validate_input(req.question, user_id=current_user.id)
    if not g_res.passed:
        raise HTTPException(status_code=400, detail={
            "blocked": True,
            "block_reason": g_res.block_reason or g_res.violation_type,
            "message": f"Guardrail triggered: {g_res.violation_type}",
        })

    research_id = uuid.uuid4().hex[:12]
    allowed_doc_ids = get_visible_doc_ids(current_user)

    try:   # stream B4: job-based engine run synchronously; same result shape
        from VENUAI.research_jobs import run_research_sync
        result = run_research_sync(
            req.question, user_id=current_user.id, tenant_id=current_user.tenant_id,
            allowed_doc_ids=allowed_doc_ids, depth="quick",
        )
    except ImportError:
        result = DeepResearchAgent().research(
            req.question, get_groq_client(), allowed_doc_ids=allowed_doc_ids
        )

    # Output guardrails + policy classifier on the synthesized answer (frozen dataclass -> replace).
    import dataclasses
    from VENUAI.answer_pipeline import apply_output_safety
    research_safe = apply_output_safety(result.synthesized_answer, None, user_id=current_user.id)
    if research_safe["blocked"] or research_safe["answer"] != result.synthesized_answer:
        result = dataclasses.replace(result, synthesized_answer=research_safe["answer"])

    mm = get_memory_manager()
    mm.save_research_finding(
        research_id=research_id, user_id=current_user.id,
        question=req.question, answer=result.synthesized_answer,
        evidence_coverage=result.evidence_coverage, critique_passed=result.critique_passed,
    )

    from VENUAI import evidence_store
    evidence_store.save_record(
        current_user.id, current_user.tenant_id, req.question, result.synthesized_answer, "research",
        evidence_store.serialize_claims(result.claims), result.evidence_coverage,
        sum(1 for c in result.claims if c.support_status.value == "unsupported"),
        sum(1 for c in result.claims if c.support_status.value == "contradicted"),
        result.critique_passed,
    )

    graph_entity_count = 0
    graph_relationship_count = 0
    if req.extract_graph and result.synthesized_answer:
        # Builds knowledge graph entities/relationships from the verified,
        # synthesized research answer (which already passed citation/
        # contradiction/critique checks — see VENUAI/verification_pipeline.py)
        # rather than raw unverified text. See Documents/AI/knowledge-graph.md
        # "Research Pipeline Integration".
        from VENUAI.evidence import Evidence, EvidenceSourceType
        from VENUAI.knowledge_graph.extraction import extract_deterministic, extract_llm
        from VENUAI.knowledge_graph.store import get_graph_store

        research_evidence = Evidence(
            evidence_id=f"research-{research_id}", document_id=f"research-{research_id}",
            document_name=f"Research: {req.question}"[:200], page_number=1,
            content=result.synthesized_answer, source_type=EvidenceSourceType.UPLOADED_DOCUMENT,
            owner_id=current_user.id, classification="UNKNOWN",
        )
        det = extract_deterministic(
            research_evidence, owner_id=current_user.id, classification="UNKNOWN", tenant_id=current_user.tenant_id,
        )
        llm = extract_llm(
            research_evidence, get_groq_client(), owner_id=current_user.id, classification="UNKNOWN",
            tenant_id=current_user.tenant_id,
        )

        store = get_graph_store()
        all_entities = det.entities + llm.entities
        all_relationships = det.relationships + llm.relationships
        for entity in all_entities:
            store.add_entity(entity)
        for rel in all_relationships:
            store.add_relationship(rel)
        graph_entity_count = len(all_entities)
        graph_relationship_count = len(all_relationships)

    return ResearchResponse(
        research_id=research_id,
        question=result.question,
        plan_is_fallback=result.plan_is_fallback,
        subquestion_results=[
            SubquestionResultOut(subquestion=r.subquestion, answer=r.answer, evidence_coverage=r.evidence_coverage)
            for r in result.subquestion_results
        ],
        synthesized_answer=result.synthesized_answer,
        evidence_coverage=result.evidence_coverage,
        critique_passed=result.critique_passed,
        truncated=result.truncated,
        graph_entities_extracted=graph_entity_count,
        graph_relationships_extracted=graph_relationship_count,
        blocked=research_safe["blocked"],
        block_reason=research_safe["block_reason"],
        safety_check=research_safe["safety_check"],
    )


@app.get("/documents", response_model=List[DocumentInfo])
def list_documents(current_user: AuthenticatedUser = Depends(get_current_user)):
    """
    Return documents the current user is authorized to see:
    admins see everything; a regular user sees their own uploads plus any
    document explicitly classified PUBLIC. See Documents/Backend/authentication.md.
    """
    meta = load_metadata()
    visible = []
    for v in meta.values():
        if v.get("tenant_id", "default") != current_user.tenant_id:
            continue
        owner_id = v.get("owner_id")
        classification = v.get("classification", "UNKNOWN")
        if current_user.role == "admin" or owner_id == current_user.id or classification == "PUBLIC":
            visible.append(v)
    return [
        DocumentInfo(
            id=v["id"],
            filename=v["filename"],
            pages=v["pages"],
            chunks=v["chunks"],
            upload_time=v["upload_time"],
            size_kb=v["size_kb"],
            tags=v.get("tags"),
        )
        for v in visible
    ]


@app.delete("/documents/{doc_id}")
def delete_document(doc_id: str, current_user: AuthenticatedUser = Depends(get_current_user)):
    """Remove a document and all its chunks. Only the owner or an admin may delete it."""
    meta = load_metadata()
    if doc_id not in meta:
        raise HTTPException(status_code=404, detail="Document not found.")
    doc_info = meta[doc_id]
    if doc_info.get("tenant_id", "default") != current_user.tenant_id:
        raise HTTPException(status_code=404, detail="Document not found.")
    if current_user.role != "admin" and doc_info.get("owner_id") != current_user.id:
        raise HTTPException(status_code=403, detail="You do not have permission to delete this document.")
    vs = get_vector_store()
    vs._collection.delete(where={"doc_id": doc_id})
    from VENUAI.retrieval import bump_corpus_version
    bump_corpus_version(current_user.tenant_id)
    try:
        from VENUAI import raptor
        if hasattr(raptor, "delete_summaries"):
            raptor.delete_summaries(doc_id, current_user.tenant_id)
    except Exception as err:  # noqa: BLE001
        logger.debug(f"raptor summary cleanup skipped: {err}")
    vs.persist()
    file_path = Path(doc_info.get("file_path", ""))
    if file_path.exists():
        file_path.unlink()
    del meta[doc_id]
    save_metadata(meta)
    logger.info(f"Deleted document {doc_id} ({doc_info['filename']}) by user {current_user.id}")
    return {"status": "deleted", "doc_id": doc_id, "filename": doc_info["filename"]}


# ── Multimodal document intelligence (Phase 2) ──────────────────────────────
# See Documents/AI/multimodal.md. Separate from the /upload RAG pipeline
# above — this does NOT embed anything into Chroma; it extracts and stores
# real page/region structure (text, tables, images) for region-level
# citation and VisionAgent querying. A document can go through both /upload
# and /upload/multimodal independently.

MAX_MULTIMODAL_PAGES = 50  # bounds processing time/memory for one request —
# a real resource limit, not a cosmetic one, since PDF parsing cost scales
# with page count and this path has no background job queue (Phase 1 ADR-003).


def _check_document_access(doc_info: dict, current_user: AuthenticatedUser) -> None:
    if doc_info.get("tenant_id", "default") != current_user.tenant_id:
        raise HTTPException(status_code=404, detail="Document not found.")
    owner_id = doc_info.get("owner_id")
    classification = doc_info.get("classification", "UNKNOWN")
    if current_user.role != "admin" and owner_id != current_user.id and classification != "PUBLIC":
        raise HTTPException(status_code=403, detail="You do not have permission to view this document.")


@app.post("/upload/multimodal")
@limiter.limit("5/minute")
async def upload_multimodal_document(
    request: Request,
    file: UploadFile = File(...),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Extract real page/table/image structure from a PDF — see
    VENUAI/multimodal_ingestion.py. Does not embed into the vector store;
    this is a separate document record from the /upload RAG pipeline.
    """
    _enforce_document_quota(current_user)
    content = await validate_upload(file)  # same magic-byte/size validation as /upload

    doc_id = str(uuid.uuid4())
    safe_name = generate_storage_name(file.filename)
    save_path = UPLOAD_DIR / f"mm_{doc_id}_{safe_name}"
    with open(save_path, "wb") as f:
        f.write(content)

    try:
        import fitz
        page_count = fitz.open(str(save_path)).page_count
        if page_count > MAX_MULTIMODAL_PAGES:
            save_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=413,
                detail=f"PDF has {page_count} pages; multimodal extraction is limited to {MAX_MULTIMODAL_PAGES} pages per request.",
            )

        from VENUAI.multimodal_ingestion import extract_multimodal_document
        mm_doc = extract_multimodal_document(str(save_path), document_id=doc_id, filename=file.filename)
    except HTTPException:
        raise
    except Exception as e:
        save_path.unlink(missing_ok=True)
        raise http_security.safe_error(e, "Failed to process PDF for multimodal extraction.", 422)

    summary = mm_doc.to_summary_dict()
    from datetime import datetime
    meta = load_metadata()
    meta[doc_id] = {
        "id": doc_id,
        "filename": file.filename,
        "safe_filename": safe_name,
        "pages": len(mm_doc.pages),
        "chunks": 0,  # not chunked/embedded — see module docstring above
        "upload_time": datetime.now().isoformat(),
        "size_kb": round(len(content) / 1024, 1),
        "file_path": str(save_path),
        "tags": {},
        "owner_id": current_user.id,
        "tenant_id": current_user.tenant_id,
        "classification": "UNKNOWN",
        "is_multimodal": True,
        "multimodal_summary": summary,
    }
    save_metadata(meta)
    logger.info(f"Multimodal-ingested {file.filename} ({summary['total_tables']} tables, "
                f"{summary['total_images']} images) by user {current_user.id}")

    return summary


@app.get("/documents/{doc_id}/regions")
def get_document_regions(doc_id: str, current_user: AuthenticatedUser = Depends(get_current_user)):
    """Return the stored region summary for a multimodal-ingested document."""
    meta = load_metadata()
    if doc_id not in meta or not meta[doc_id].get("is_multimodal"):
        raise HTTPException(status_code=404, detail="No multimodal data found for this document.")
    _check_document_access(meta[doc_id], current_user)
    return meta[doc_id]["multimodal_summary"]


class RegionQuestionRequest(BaseModel):
    question: str


class RegionQuestionResponse(BaseModel):
    answer: str
    insufficient_visual_evidence: bool
    basis: str


@app.post("/documents/{doc_id}/regions/{region_id}/ask", response_model=RegionQuestionResponse)
@limiter.limit("20/minute")
def ask_about_region(
    request: Request,
    doc_id: str,
    region_id: str,
    req: RegionQuestionRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    VisionAgent over one stored region — see
    VENUAI/agents/vision_agent.py. Answers from the region's real extracted
    text (table text or OCR); returns insufficient_visual_evidence=True
    rather than fabricating an answer when the region has no usable text.
    """
    from VENUAI.agents.vision_agent import VisionAgent
    from VENUAI.multimodal_document import BoundingBox, DocumentRegion, RegionType

    meta = load_metadata()
    if doc_id not in meta or not meta[doc_id].get("is_multimodal"):
        raise HTTPException(status_code=404, detail="No multimodal data found for this document.")
    _check_document_access(meta[doc_id], current_user)

    region_dict = next(
        (r for page in meta[doc_id]["multimodal_summary"]["pages"] for r in page["regions"]
         if r["region_id"] == region_id),
        None,
    )
    if region_dict is None:
        raise HTTPException(status_code=404, detail="Region not found.")

    bbox = region_dict.get("bounding_box")
    region = DocumentRegion(
        document_id=doc_id,
        page_number=0,
        region_id=region_id,
        region_type=RegionType(region_dict["region_type"]),
        bounding_box=BoundingBox(**bbox) if bbox else None,
        extracted_text=region_dict.get("extracted_text", ""),
        ocr_text=region_dict.get("ocr_text"),
        ocr_unavailable_reason=region_dict.get("ocr_unavailable_reason"),
    )

    result = VisionAgent().analyze_region(region, req.question, groq_client=get_groq_client())
    return RegionQuestionResponse(
        answer=result.answer,
        insufficient_visual_evidence=result.insufficient_visual_evidence,
        basis=result.basis,
    )


class RegionSearchRequest(BaseModel):
    query: str
    top_k: int = 5


class RegionSearchResultOut(BaseModel):
    document_id: str
    page_number: int
    region_type: str
    content: str
    score: float


@app.post("/documents/regions/search", response_model=List[RegionSearchResultOut])
@limiter.limit("30/minute")
def search_regions(
    request: Request,
    req: RegionSearchRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Lexical search across TABLE/IMAGE regions of every multimodal document
    the caller can see — see VENUAI/multimodal_retrieval.py. This is the
    "multimodal retrieval" channel for table/OCR'd-image text; it is not
    yet wired into the main /query RAG pipeline (standard /query only
    searches text chunks in Chroma) — see Documents/AI/multimodal.md.
    """
    from VENUAI.multimodal_retrieval import search_multimodal_regions

    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    results = search_multimodal_regions(
        req.query, load_metadata(), get_visible_doc_ids(current_user), top_k=req.top_k,
    )
    return [
        RegionSearchResultOut(
            document_id=r.evidence.document_id,
            page_number=r.evidence.page_number,
            region_type=r.evidence.modality.value,
            content=r.evidence.content,
            score=r.score,
        )
        for r in results
    ]


# ── Data Analyst Agent — dataset upload/management/analysis ────────────────
# See Documents/AI/data-analyst.md. Separate from the RAG document store
# above (DATASET_DIR/DATASET_META_FILE) — see the comment at their
# definitions for why. The pandas computation logic itself lives entirely
# in VENUAI/agents/data_analyst_agent.py and is not duplicated here.

class DatasetInfo(BaseModel):
    id: str
    filename: str
    columns: List[str]
    dtypes: Dict[str, str]
    row_count: int
    size_kb: float
    upload_time: str
    classification: str = "UNKNOWN"


class DatasetAnalyzeRequest(BaseModel):
    question: str


class DatasetAnalyzeResponse(BaseModel):
    operation: str
    computed_values: Dict[str, Any]
    interpretation: str
    referenced_columns: List[str]
    chart_data: Optional[Dict[str, Any]] = None


def _check_dataset_access(dataset_info: dict, current_user: AuthenticatedUser) -> None:
    if dataset_info.get("tenant_id", "default") != current_user.tenant_id:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    owner_id = dataset_info.get("owner_id")
    classification = dataset_info.get("classification", "UNKNOWN")
    if current_user.role != "admin" and owner_id != current_user.id and classification != "PUBLIC":
        raise HTTPException(status_code=403, detail="You do not have permission to access this dataset.")


@app.post("/datasets/upload", response_model=DatasetInfo)
@limiter.limit("10/minute")
async def upload_dataset(
    request: Request,
    file: UploadFile = File(...),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Upload a CSV/XLSX dataset for the Data Analyst Agent. Validated by
    VENUAI/dataset_security.py (extension, size, magic bytes for XLSX, real
    parse + row/column caps for both). Uploader becomes the owner."""
    from VENUAI.dataset_security import validate_and_parse_dataset, sanitize_filename, generate_storage_name

    _enforce_dataset_quota(current_user)
    content, df, ext = await validate_and_parse_dataset(file)

    dataset_id = str(uuid.uuid4())
    safe_name = generate_storage_name(file.filename, ext)
    save_path = DATASET_DIR / f"{dataset_id}_{safe_name}"
    with open(save_path, "wb") as f:
        f.write(content)

    from datetime import datetime
    dtypes = {col: str(dtype) for col, dtype in df.dtypes.items()}
    meta = load_dataset_metadata()
    meta[dataset_id] = {
        "id": dataset_id,
        "filename": file.filename,
        "safe_filename": safe_name,
        "file_path": str(save_path),
        "columns": list(df.columns),
        "dtypes": dtypes,
        "row_count": len(df),
        "size_kb": round(len(content) / 1024, 1),
        "upload_time": datetime.now().isoformat(),
        "owner_id": current_user.id,
        "tenant_id": current_user.tenant_id,
        "classification": "UNKNOWN",
    }
    save_dataset_metadata(meta)
    logger.info(f"Dataset uploaded: {file.filename} ({len(df)} rows, {len(df.columns)} cols) by user {current_user.id}")

    return DatasetInfo(
        id=dataset_id, filename=file.filename, columns=list(df.columns), dtypes=dtypes,
        row_count=len(df), size_kb=meta[dataset_id]["size_kb"],
        upload_time=meta[dataset_id]["upload_time"], classification="UNKNOWN",
    )


@app.get("/datasets", response_model=List[DatasetInfo])
def list_datasets(current_user: AuthenticatedUser = Depends(get_current_user)):
    """List datasets the caller can see — same ownership/PUBLIC rule as
    GET /documents."""
    meta = load_dataset_metadata()
    visible = [
        v for v in meta.values()
        if v.get("tenant_id", "default") == current_user.tenant_id
        and (current_user.role == "admin" or v.get("owner_id") == current_user.id
             or v.get("classification") == "PUBLIC")
    ]
    return [
        DatasetInfo(
            id=v["id"], filename=v["filename"], columns=v["columns"], dtypes=v["dtypes"],
            row_count=v["row_count"], size_kb=v["size_kb"], upload_time=v["upload_time"],
            classification=v.get("classification", "UNKNOWN"),
        )
        for v in visible
    ]


@app.delete("/datasets/{dataset_id}")
def delete_dataset(dataset_id: str, current_user: AuthenticatedUser = Depends(get_current_user)):
    """Remove a dataset — owner or admin only."""
    meta = load_dataset_metadata()
    if dataset_id not in meta:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    dataset_info = meta[dataset_id]
    _check_dataset_access(dataset_info, current_user)

    file_path = Path(dataset_info.get("file_path", ""))
    if file_path.exists():
        file_path.unlink()
    del meta[dataset_id]
    save_dataset_metadata(meta)
    logger.info(f"Deleted dataset {dataset_id} ({dataset_info['filename']}) by user {current_user.id}")
    return {"status": "deleted", "id": dataset_id, "filename": dataset_info["filename"]}


@app.post("/datasets/{dataset_id}/analyze", response_model=DatasetAnalyzeResponse)
@limiter.limit("20/minute")
def analyze_dataset(
    request: Request,
    dataset_id: str,
    req: DatasetAnalyzeRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Run VENUAI.agents.data_analyst_agent.DataAnalystAgent against a
    previously-uploaded dataset. The pandas computation itself is entirely
    in that module — this route only loads the stored file, enforces
    access control, and shapes the result for the API/frontend.
    """
    import pandas as pd
    from VENUAI.agents.data_analyst_agent import DataAnalystAgent
    from VENUAI.guardrails import get_guardrail_engine

    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    meta = load_dataset_metadata()
    if dataset_id not in meta:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    dataset_info = meta[dataset_id]
    _check_dataset_access(dataset_info, current_user)

    ge = get_guardrail_engine()
    g_res = ge.validate_input(req.question, user_id=current_user.id)
    if not g_res.passed:
        raise HTTPException(status_code=400, detail=f"Guardrail triggered: {g_res.violation_type}")

    file_path = Path(dataset_info["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Dataset file is missing on disk.")
    ext = file_path.suffix.lower()
    df = pd.read_csv(file_path) if ext == ".csv" else pd.read_excel(file_path)

    result = DataAnalystAgent().analyze(df, req.question, groq_client=get_groq_client())

    return DatasetAnalyzeResponse(
        operation=result.operation,
        computed_values=result.computed_values,
        interpretation=result.interpretation,
        referenced_columns=result.referenced_columns,
        chart_data=result.to_chart_data(),
    )


# ── Knowledge Graph (Phase 3) ────────────────────────────────────────────────
# See Documents/AI/knowledge-graph.md. Authorization reuses the same
# owner_id/classification rule as documents/datasets/multimodal regions.

class GraphExtractRequest(BaseModel):
    text: str
    document_name: str = "Manual input"
    use_llm: bool = True


class EntityOut(BaseModel):
    entity_id: str
    entity_type: str
    name: str
    classification: str


class RelationshipOut(BaseModel):
    relationship_id: str
    source_entity_id: str
    target_entity_id: str
    relationship_type: str
    source_evidence_id: Optional[str] = None
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    observed_at: Optional[str] = None


class GraphExtractResponse(BaseModel):
    entities: List[EntityOut]
    relationships: List[RelationshipOut]
    is_fallback: bool = False


class EntityWithRelationshipsResponse(BaseModel):
    entity: EntityOut
    relationships: List[RelationshipOut]
    related_entities: List[EntityOut] = []


def _entity_out(e) -> EntityOut:
    return EntityOut(entity_id=e.entity_id, entity_type=e.entity_type.value, name=e.name, classification=e.classification)


def _relationship_out(r) -> RelationshipOut:
    return RelationshipOut(
        relationship_id=r.relationship_id, source_entity_id=r.source_entity_id,
        target_entity_id=r.target_entity_id, relationship_type=r.relationship_type.value,
        source_evidence_id=r.source_evidence_id, valid_from=r.valid_from,
        valid_until=r.valid_until, observed_at=r.observed_at,
    )


@app.post("/graph/extract", response_model=GraphExtractResponse)
@limiter.limit("20/minute")
def extract_graph(
    request: Request,
    req: GraphExtractRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Extract entities/relationships from text and store them — see
    VENUAI/knowledge_graph/extraction.py for the deterministic (regex) and
    LLM-based extractors, both of which only ever produce
    evidence-traceable output, never invented relationships.
    """
    from VENUAI.evidence import Evidence, EvidenceSourceType
    from VENUAI.guardrails import get_guardrail_engine
    from VENUAI.knowledge_graph.extraction import extract_deterministic, extract_llm
    from VENUAI.knowledge_graph.store import get_graph_store

    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty.")

    ge = get_guardrail_engine()
    g_res = ge.validate_input(req.text, user_id=current_user.id)
    if not g_res.passed:
        raise HTTPException(status_code=400, detail=f"Guardrail triggered: {g_res.violation_type}")

    evidence = Evidence(
        evidence_id=uuid.uuid4().hex[:16], document_id="manual", document_name=req.document_name,
        page_number=1, content=req.text, source_type=EvidenceSourceType.UPLOADED_DOCUMENT,
        owner_id=current_user.id, classification="UNKNOWN",
    )

    det_result = extract_deterministic(
        evidence, owner_id=current_user.id, classification="UNKNOWN", tenant_id=current_user.tenant_id,
    )
    is_fallback = False
    all_entities = list(det_result.entities)
    all_relationships = list(det_result.relationships)

    if req.use_llm:
        llm_result = extract_llm(
            evidence, get_groq_client(), owner_id=current_user.id, classification="UNKNOWN",
            tenant_id=current_user.tenant_id,
        )
        all_entities += llm_result.entities
        all_relationships += llm_result.relationships
        is_fallback = llm_result.is_fallback

    store = get_graph_store()
    for entity in all_entities:
        store.add_entity(entity)
    for rel in all_relationships:
        store.add_relationship(rel)

    logger.info(f"[Graph] Extracted {len(all_entities)} entities, {len(all_relationships)} relationships "
                f"from text by user {current_user.id}")

    return GraphExtractResponse(
        entities=[_entity_out(e) for e in all_entities],
        relationships=[_relationship_out(r) for r in all_relationships],
        is_fallback=is_fallback,
    )


@app.get("/graph/entities/{entity_id}", response_model=EntityWithRelationshipsResponse)
def get_graph_entity(
    entity_id: str,
    as_of: Optional[str] = None,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Get one entity and its relationships, optionally as of a past date
    (spec section 23 — 'what was the status in 2024?')."""
    from datetime import datetime as _datetime
    from VENUAI.knowledge_graph.store import get_graph_store

    store = get_graph_store()
    allowed_owner_ids = None if current_user.role == "admin" else [current_user.id]

    entity = store.get_entity(entity_id, allowed_owner_ids=allowed_owner_ids, tenant_id=current_user.tenant_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="Entity not found.")

    as_of_dt = None
    if as_of:
        try:
            as_of_dt = _datetime.fromisoformat(as_of)
        except ValueError:
            raise HTTPException(status_code=400, detail="as_of must be an ISO date/datetime string.")

    relationships = store.get_relationships_for_entity(
        entity_id, as_of=as_of_dt, allowed_owner_ids=allowed_owner_ids, tenant_id=current_user.tenant_id,
    )

    # Resolve the "other side" of each relationship too — the relationship
    # record itself only carries entity ids, not names/types, and the
    # frontend's graph visualization (Research Workspace's "Knowledge
    # Graph" tab) needs a label for every node it draws, not just the
    # center one. Same authorization applied per lookup as the center
    # entity itself — a neighbor the caller isn't allowed to see is
    # silently omitted, not just unlabeled.
    neighbor_ids = {
        other_id
        for r in relationships
        for other_id in (r.source_entity_id, r.target_entity_id)
        if other_id != entity_id
    }
    related_entities = [
        e for e in (
            store.get_entity(nid, allowed_owner_ids=allowed_owner_ids, tenant_id=current_user.tenant_id)
            for nid in neighbor_ids
        ) if e is not None
    ]

    return EntityWithRelationshipsResponse(
        entity=_entity_out(entity),
        relationships=[_relationship_out(r) for r in relationships],
        related_entities=[_entity_out(e) for e in related_entities],
    )


@app.get("/graph/search", response_model=List[EntityOut])
def search_graph_entities(
    q: str = "",
    entity_type: Optional[str] = None,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Search entities by name substring, optionally filtered by type."""
    from VENUAI.knowledge_graph.models import EntityType as _EntityType
    from VENUAI.knowledge_graph.store import get_graph_store

    parsed_type = None
    if entity_type:
        try:
            parsed_type = _EntityType(entity_type)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown entity_type '{entity_type}'.")

    store = get_graph_store()
    allowed_owner_ids = None if current_user.role == "admin" else [current_user.id]
    entities = store.find_entities(
        name_query=q or None, entity_type=parsed_type, allowed_owner_ids=allowed_owner_ids,
        tenant_id=current_user.tenant_id,
    )
    return [_entity_out(e) for e in entities]


@app.get("/kb/preset")
def get_preset_kb():
    """Return preloaded DRDO Master Knowledge Base JSON data."""
    kb_data = load_prefilled_kb()
    if not kb_data:
        raise HTTPException(status_code=404, detail="Prefilled DRDO Knowledge Base file not found.")
    return kb_data


@app.get("/analytics/dashboard")
def analytics_dashboard(current_user: AuthenticatedUser = Depends(require_admin)):
    """Return aggregated analytics data for the admin dashboard (admin-only)."""
    from VENUAI.analytics import get_analytics
    return get_analytics().get_dashboard_data()


# ── Phase 4 — read-only registry/security UI endpoints ──────────────────────
# Backs the frontend's Agent/Model Control Panel and Security Center. These
# are read-only views over real Phase 1/3 registries and the real guardrail
# audit log (VENUAI/guardrails.py) — no new state is introduced here.

class AgentOut(BaseModel):
    agent_id: str
    name: str
    description: str
    version: str
    allowed_tools: List[str]
    required_permissions: List[str]
    risk_level: str
    max_steps: int
    max_runtime_seconds: int
    enabled: bool


@app.get("/agents", response_model=List[AgentOut])
def list_agents(current_user: AuthenticatedUser = Depends(get_current_user)):
    """Every agent registered in VENUAI/agents/registry.py — see Documents/AI/agents.md."""
    from VENUAI.agents.registry import all_agents
    return [
        AgentOut(
            agent_id=a.agent_id, name=a.name, description=a.description, version=a.version,
            allowed_tools=a.allowed_tools, required_permissions=a.required_permissions,
            risk_level=a.risk_level.value, max_steps=a.max_steps,
            max_runtime_seconds=a.max_runtime_seconds, enabled=a.enabled,
        )
        for a in all_agents()
    ]


class ModelOut(BaseModel):
    model_id: str
    provider: str
    capabilities: List[str]
    context_window: int
    max_output_tokens: int
    privacy_class: str
    cost_tier: str
    latency_class: str
    enabled: bool
    notes: str


@app.get("/models", response_model=List[ModelOut])
def list_models(current_user: AuthenticatedUser = Depends(get_current_user)):
    """The real model catalog — VENUAI/ai_gateway/model_registry.py. `enabled`
    reflects live provider configuration, not an aspirational listing."""
    from VENUAI.ai_gateway.model_registry import get_catalog
    return [
        ModelOut(
            model_id=m.model_id, provider=m.provider,
            capabilities=[c.value for c in m.capabilities],
            context_window=m.context_window, max_output_tokens=m.max_output_tokens,
            privacy_class=m.privacy_class.value, cost_tier=m.cost_tier,
            latency_class=m.latency_class, enabled=m.enabled, notes=m.notes,
        )
        for m in get_catalog()
    ]


class SecurityViolationOut(BaseModel):
    id: int
    timestamp: str
    user_id: Optional[str] = None
    query: Optional[str] = None
    violation_type: Optional[str] = None
    action_taken: Optional[str] = None
    message: Optional[str] = None


@app.get("/security/violations", response_model=List[SecurityViolationOut])
def list_security_violations(
    limit: int = 100, current_user: AuthenticatedUser = Depends(require_admin),
):
    """
    Recent guardrail violations (prompt injection, PII, sensitivity),
    admin-only and scoped to the admin's own tenant (Phase 4 — admin is a
    per-tenant role, not a cross-tenant superuser; see
    Documents/Backend/multi-tenancy.md). guardrail_logs itself has no
    tenant_id column, so scoping is done by joining against the set of
    user_ids that actually belong to this tenant.
    """
    from VENUAI.guardrails import get_guardrail_engine

    tenant_user_ids = auth.get_user_ids_in_tenant(current_user.tenant_id)
    rows = get_guardrail_engine().get_recent_violations(user_ids=tenant_user_ids, limit=limit)
    return [SecurityViolationOut(**r) for r in rows]


# ── Evidence Explorer (Phase 4) ──────────────────────────────────────────────
# See Documents/AI/evidence-explorer.md and VENUAI/evidence_store.py. Browses
# the claim-by-claim verification breakdown (SUPPORTED/UNSUPPORTED/
# CONTRADICTED, with the real Evidence backing each claim) that standard
# RAG (/query) and Deep Research (/research) already compute via
# VerificationPipeline but previously discarded after the response was sent.

class EvidenceRecordSummary(BaseModel):
    record_id: str
    user_id: str
    tenant_id: str
    question: str
    source: str
    evidence_coverage: float
    unsupported_claim_count: int
    contradicted_claim_count: int
    critique_passed: bool
    created_at: float


class EvidenceItemOut(BaseModel):
    evidence_id: str
    document_name: str
    page_number: int
    content: str
    modality: str
    relevance_score: float


class ClaimOut(BaseModel):
    claim_id: str
    text: str
    support_status: str
    support_score: float
    evidence: List[EvidenceItemOut]


class EvidenceRecordDetail(EvidenceRecordSummary):
    answer: str
    claims: List[ClaimOut]


@app.get("/evidence", response_model=List[EvidenceRecordSummary])
def list_evidence_records(limit: int = 50, current_user: AuthenticatedUser = Depends(get_current_user)):
    """
    List past verification records — a regular user sees their own;
    admin sees every record in their own tenant (the same per-tenant-not-
    cross-tenant rule as the rest of Phase 4).
    """
    from VENUAI import evidence_store
    user_filter = None if current_user.role == "admin" else current_user.id
    records = evidence_store.list_records(current_user.tenant_id, user_filter, limit=limit)
    return [EvidenceRecordSummary(**r) for r in records]


@app.get("/evidence/{record_id}", response_model=EvidenceRecordDetail)
def get_evidence_record(record_id: str, current_user: AuthenticatedUser = Depends(get_current_user)):
    """Full claim/evidence breakdown for one past answer."""
    from VENUAI import evidence_store
    user_filter = None if current_user.role == "admin" else current_user.id
    record = evidence_store.get_record(record_id, current_user.tenant_id, user_filter)
    if record is None:
        raise HTTPException(status_code=404, detail="Evidence record not found.")
    return EvidenceRecordDetail(**record)


# ── Entry Point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("rag_server:app", host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")),
                reload=False, server_header=False, timeout_keep_alive=15, limit_concurrency=int(os.getenv("LIMIT_CONCURRENCY", "200")))
