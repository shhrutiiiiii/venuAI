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
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

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
app = FastAPI(
    title="DRDO VenuAI RAG Server",
    description="Knowledge Base RAG pipeline for DRDO research documents",
    version="1.1.0",
)

# CORS origins are environment-driven, not hardcoded, so this can run anywhere
# other than a developer's own machine without editing source. See
# Documents/Backend/architecture.md Security Considerations.
_allowed_origins = [
    o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Rate limiting ────────────────────────────────────────────────────────────
# Prevents brute-force login attempts and abusive/expensive query floods.
# See Documents/future-additions.md Track A item 10.
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

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

    def invoke(self, prompt_text: str):
        from types import SimpleNamespace
        from VENUAI.ai_gateway.gateway import get_gateway
        from VENUAI.ai_gateway.model_router import TaskType
        result = get_gateway().complete(
            task_type=TaskType.GENERATION,
            messages=[{"role": "user", "content": prompt_text}],
            max_tokens=1500,
            temperature=0.2,
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


class DocumentInfo(BaseModel):
    id: str
    filename: str
    pages: int
    chunks: int
    upload_time: str
    size_kb: float
    tags: Optional[dict] = None


# ── Auth Routes ────────────────────────────────────────────────────────────────
# Real, server-side authentication — see Documents/Backend/authentication.md.
# Logic lives in VENUAI/auth.py, oauth.py, email_service.py; handlers are thin.
from VENUAI.client_ip import trusted_client_ip  # noqa: E402
from VENUAI import security_audit  # noqa: E402


def _token_response(user_row, access: str, refresh: str) -> auth.TokenResponse:
    return auth.TokenResponse(
        access_token=access,
        refresh_token=refresh,
        user=auth.UserOut(id=user_row["id"], email=user_row["email"], role=user_row["role"], tenant_id=user_row["tenant_id"]),
    )


@app.post("/auth/register", response_model=auth.UserOut, status_code=201)
@limiter.limit("5/minute")
def register(request: Request, body: auth.RegisterRequest):
    return auth.register_user(
        body.email, body.password,
        organization_name=body.organization_name,
        tenant_id=body.tenant_id,
        invite_token=body.invite_token,
        ip=trusted_client_ip(request),
    )


@app.post("/auth/login", response_model=auth.TokenResponse)
@limiter.limit("10/minute")
def login(request: Request, body: auth.LoginRequest):
    # Unknown email / wrong password / locked account all raise the same 401.
    user_row = auth.authenticate(body.email, body.password, ip=trusted_client_ip(request))
    access = auth.create_access_token(user_row["id"], user_row["role"], user_row["token_version"])
    refresh = auth.issue_refresh_token(user_row["id"])
    return _token_response(user_row, access, refresh)


@app.post("/auth/refresh", response_model=auth.TokenResponse)
@limiter.limit("20/minute")
def refresh_token(request: Request, body: auth.RefreshRequest):
    new_access, new_refresh = auth.rotate_refresh_token(body.refresh_token, ip=trusted_client_ip(request))
    payload = auth.decode_access_token(new_access)
    return _token_response(auth.get_user_by_id(payload["sub"]), new_access, new_refresh)


@app.post("/auth/logout", status_code=204)
@limiter.limit("30/minute")
def logout(request: Request, body: auth.RefreshRequest):
    """Revokes the whole refresh-token family and bumps token_version so the
    session's access tokens die immediately, not at expiry."""
    user_id = auth.revoke_refresh_token(body.refresh_token)
    if user_id:
        security_audit.log("logout", user_id, None, trusted_client_ip(request))
    return None


@app.post("/auth/logout-all", status_code=204)
@limiter.limit("10/minute")
def logout_all(request: Request, current_user: AuthenticatedUser = Depends(get_current_user)):
    auth.logout_all(current_user.id)
    security_audit.log("logout_all", current_user.id, current_user.tenant_id, trusted_client_ip(request))
    return None


# ── OAuth (Google / GitHub) ──────────────────────────────────────────────────
# Logic in VENUAI/oauth.py (PKCE, browser-bound single-use state, verified
# email, sub-keyed identities). The Next.js routes own the browser redirect and
# the httpOnly cookies (session + short-lived oauth binding).

class OAuthAuthorizeResponse(BaseModel):
    authorize_url: str
    binding: str


@app.get("/auth/oauth/{provider}/login", response_model=OAuthAuthorizeResponse)
@limiter.limit("20/minute")
def oauth_login(request: Request, provider: str, redirect_uri: str):
    """Returns the provider's authorization URL plus a `binding` secret the
    frontend must keep in an httpOnly cookie and echo on callback. 503 if the
    provider isn't configured — never a fabricated URL."""
    from VENUAI.oauth import OAuthNotConfiguredError, begin_authorization

    try:
        url, binding = begin_authorization(provider, redirect_uri)
        return OAuthAuthorizeResponse(authorize_url=url, binding=binding)
    except OAuthNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class OAuthCallbackRequest(BaseModel):
    code: str
    state: str
    redirect_uri: str
    binding: Optional[str] = None


@app.post("/auth/oauth/{provider}/callback", response_model=auth.TokenResponse)
@limiter.limit("10/minute")
def oauth_callback(request: Request, provider: str, body: OAuthCallbackRequest):
    from VENUAI.oauth import OAuthExchangeError, OAuthLoginError, OAuthNotConfiguredError, complete_login

    try:
        user_row = complete_login(
            provider, body.code, body.state, body.binding, body.redirect_uri, ip=trusted_client_ip(request)
        )
    except OAuthLoginError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except OAuthNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except OAuthExchangeError as e:
        logger.warning(f"[oauth_callback] provider exchange failed: {e}")
        raise HTTPException(status_code=502, detail="Sign-in with the provider failed. Please try again.")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    access_token = auth.create_access_token(user_row["id"], user_row["role"], user_row["token_version"])
    return _token_response(user_row, access_token, auth.issue_refresh_token(user_row["id"]))


# ── Password reset ───────────────────────────────────────────────────────────
@app.post("/auth/forgot-password", status_code=202)
@limiter.limit("5/minute")
def forgot_password(request: Request, body: auth.ForgotPasswordRequest, background_tasks: BackgroundTasks):
    """Identical 202 for known, unknown and SMTP-failing cases; all real work
    (lookup, token, send) runs after the response so timing matches too.
    Failures are logged server-side only."""
    from VENUAI import email_service

    background_tasks.add_task(email_service.send_password_reset, body.email, trusted_client_ip(request))
    return {"status": "accepted"}


@app.post("/auth/reset-password", status_code=200)
@limiter.limit("10/minute")
def reset_password(request: Request, body: auth.ResetPasswordRequest):
    """Single-use token bound to the current password hash: validates the new
    password against the policy first (a weak password does not burn the
    token), then atomically spends the token, then updates the password
    (which kills every session)."""
    invalid = HTTPException(status_code=400, detail="This password reset link is invalid or has expired.")
    ip = trusted_client_ip(request)
    user_id = auth.verify_password_reset_token(body.token)
    user_row = auth.get_user_by_id(user_id) if user_id else None
    if user_row is None:
        security_audit.log("reset_rejected", None, None, ip)
        raise invalid
    auth.enforce_password_policy(body.new_password, user_row["email"])
    if auth.consume_password_reset_token(body.token) != user_id:
        security_audit.log("reset_rejected", user_id, user_row["tenant_id"], ip)
        raise invalid
    auth.update_password(user_id, body.new_password)
    security_audit.log("reset_used", user_id, user_row["tenant_id"], ip)
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


@app.get("/health")
def health_check():
    meta = load_metadata()
    return {
        "status": "ok",
        "service": "DRDO VenuAI RAG Server",
        "version": "1.1.0",
        "documents_ingested": len(meta),
        "vector_store": "ChromaDB (local)",
        "embedding_model": get_embedding_model_name(),
        "llm": _llm_health(),
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
        raise HTTPException(status_code=422, detail=f"Failed to parse PDF: {str(e)}")

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
    History-aware rewrite -> dense + cached BM25 -> RRF -> cross-encoder
    rerank -> relevance floor. All of it lives in VENUAI.retrieval (shared
    with agentic_rag / agent_tools). Authorization: allowed_doc_ids filters
    every stage; an empty list retrieves nothing.
    """
    from VENUAI.retrieval import retrieve_documents

    question = state["question"]
    top_k = state.get("top_k", 4)
    res = retrieve_documents(
        get_vector_store(), question, top_k, state.get("allowed_doc_ids"),
        history=state.get("history") or state.get("working_memory") or [],
    )
    return {
        "documents": res.documents,
        "retrieval_query": res.retrieval_query,
        "retrieval_scores": res.scores,
        "insufficient_context": res.insufficient_context,
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


def _kb_fallback_docs() -> List[Document]:
    """Built-in KB as a clearly-labelled document, or [] if no KB is available."""
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


def generate_node(state: RAGGraphState) -> dict:
    """
    Generate an answer using Context Engineering for optimal prompt quality.
    Integrates: rank_chunks, compress_history, build_drdo_prompt, semantic_memories.
    The raw answer (still carrying the <citations> block) is post-processed in verify_node.
    """
    from VENUAI.context_engineer import (
        ContextBudget, rank_chunks, compress_history, build_drdo_prompt
    )
    from VENUAI.answer_pipeline import ANSWER_BASIS_DOCUMENTS, ANSWER_BASIS_KB, ANSWER_BASIS_NONE

    question = state["question"]
    docs     = state.get("documents", [])
    history  = state.get("history") or []
    sem_mems = state.get("semantic_memories", [])

    ranked_docs = rank_chunks(docs, question)
    answer_basis = ANSWER_BASIS_DOCUMENTS if ranked_docs else ANSWER_BASIS_NONE
    if not ranked_docs:
        ranked_docs = _kb_fallback_docs()
        if ranked_docs:
            answer_basis = ANSWER_BASIS_KB

    compressed_history = compress_history(history, max_turns=6)
    budget             = ContextBudget()
    prompt_info: dict  = {}
    prompt_text        = build_drdo_prompt(
        question=question,
        ranked_docs=ranked_docs,
        compressed_history=compressed_history,
        budget=budget,
        semantic_memories=sem_mems or [],
        info=prompt_info,
    )
    prompt_docs = prompt_info.get("used_docs", [])

    llm = get_llm()
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
                        user_id: str, answer_basis: str, light: bool = False) -> dict:
    """
    Shared tail for every answer path: split + verify citations, verify the answer
    (fail-closed), then output guardrails + policy classifier. Returns graph-state fields.
    """
    from VENUAI.answer_pipeline import (
        split_citations, run_verification, apply_output_safety, ANSWER_BASIS_KB, KB_NOTICE,
    )
    from VENUAI.grounded_citations import verify_citations

    clean, quotes = split_citations(raw_answer)
    ver = run_verification(clean, question, prompt_docs, light=light)
    final_answer = ver.pop("final_answer")
    citations = verify_citations(final_answer, quotes, prompt_docs)
    safe = apply_output_safety(final_answer, ver.get("grounding_score"), user_id=user_id)
    if safe["blocked"]:
        ver["verified"] = False
        ver["verification_reason"] = "answer withheld by output safety policy"
        citations = []
    result = {
        **ver,
        "answer": safe["answer"],
        "annotated_answer": safe["answer"] if safe["blocked"] else (ver.get("annotated_answer") or safe["answer"]),
        "citations": citations,
        "safety_check": safe["safety_check"],
        "blocked": safe["blocked"],
        "block_reason": safe["block_reason"],
    }
    if answer_basis == ANSWER_BASIS_KB and not safe["blocked"]:
        result["answer"] = f"_{KB_NOTICE}_\n\n{result['answer']}"
        result["annotated_answer"] = f"_{KB_NOTICE}_\n\n{result['annotated_answer']}"
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
    )


def fallback_kb_node(state: RAGGraphState) -> dict:
    """No uploaded documents at all: answer from the built-in KB, labelled as such, and
    verify against the KB text actually used (no fixed scores)."""
    from VENUAI.context_engineer import ContextBudget, build_drdo_prompt
    from VENUAI.answer_pipeline import ANSWER_BASIS_KB

    kb_docs = _kb_fallback_docs()
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


@app.post("/query", response_model=QueryResponse)
@limiter.limit("30/minute")
def query_knowledge_base(
    request: Request,
    req: QueryRequest,
    background_tasks: "BackgroundTasks",
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Run a RAG query. Supports standard RAG, ReAct agent, and multi-agent modes.
    Integrates memory management, verification, hallucination detection, and analytics.
    """
    import time as _time
    from fastapi import BackgroundTasks
    from VENUAI.observability import RequestTrace, new_request_id

    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    # The authenticated user's own ID is the source of truth for identity —
    # req.user_id is client-supplied and must never be trusted as an identity
    # claim (see Documents/Backend/authentication.md Design Principle 4).
    req.user_id = current_user.id

    trace = RequestTrace(request_id=new_request_id(), route="/query", user_id=current_user.id)

    # Guardrails Input Check
    from VENUAI.guardrails import get_guardrail_engine
    ge = get_guardrail_engine()
    g_res = ge.validate_input(req.question, user_id=req.user_id or "anonymous")
    if not g_res.passed:
        trace.log_summary(success=False, extra={"stage": "guardrail_blocked", "violation": g_res.violation_type})
        return QueryResponse(
            answer=g_res.sanitized_input_or_output,
            sources=[],
            model="DRDO Defence Guardrails Policy",
            chunks_retrieved=0,
            grounding_score=None,
            hallucination_score=None,
            verification_flags=[f"GUARDRAIL_TRIGGERED: {g_res.violation_type}"],
            annotated_answer=g_res.sanitized_input_or_output,
            blocked=True,
            block_reason=g_res.block_reason or g_res.violation_type,
            verified=False,
            verification_reason="request blocked by input guardrails",
            trace_id=trace.request_id,
        )

    t_start = _time.time()
    agent_used = "rag"
    tools_called: List[str] = []
    error_str = None

    # Fetch memory context
    from VENUAI.memory_manager import get_memory_manager
    mm = get_memory_manager()
    mem_ctx = mm.get_context(req.session_id, req.user_id or "anonymous", req.question)
    working_memory   = mem_ctx.get("working", [])
    semantic_memories = mem_ctx.get("semantic", [])
    effective_history = req.history or working_memory or []

    try:
        # Mode: Multi-Agent
        if req.multi_agent_mode:
            import asyncio
            from VENUAI.agents.orchestrator import OrchestratorAgent
            orchestrator = OrchestratorAgent(get_groq_client())
            agent_result = asyncio.run(
                orchestrator.route(
                    req.question, effective_history, mem_ctx,
                    allowed_doc_ids=get_visible_doc_ids(current_user),
                )
            )
            agent_used = "multi_agent"

            # Phase 5 — multi-agent mode now runs the same claim-level
            # VerificationPipeline standard RAG does, using the real
            # Documents the specialist(s) actually retrieved
            # (AgentResult.documents — previously discarded after building
            # QuerySource summaries). Best-effort: a verification failure
            # must not turn a successful multi-agent answer into a 500.
            from VENUAI import evidence_store
            evidence_coverage, unsupported_count, contradicted_count, critique_passed = None, None, None, False
            from VENUAI.answer_pipeline import run_verification, apply_output_safety, ANSWER_BASIS_DOCUMENTS, ANSWER_BASIS_NONE
            ma_ver = run_verification(agent_result.answer, req.question, agent_result.documents, light=True)
            ma_safe = apply_output_safety(ma_ver.pop("final_answer"), ma_ver.get("grounding_score"), user_id=current_user.id)
            agent_result.answer = ma_safe["answer"]
            try:
                from VENUAI.verification_pipeline import get_verification_pipeline
                pipeline_result = get_verification_pipeline().run(
                    req.question, agent_result.answer, agent_result.documents,
                )
                evidence_coverage = pipeline_result.evidence_graph.evidence_coverage
                unsupported_count = pipeline_result.critique.unsupported_claim_count
                contradicted_count = pipeline_result.critique.contradicted_claim_count
                critique_passed = pipeline_result.critique.passed
                background_tasks.add_task(
                    evidence_store.save_record,
                    current_user.id, current_user.tenant_id, req.question, agent_result.answer, "multi_agent",
                    evidence_store.serialize_claims(pipeline_result.evidence_graph.claims),
                    evidence_coverage, unsupported_count, contradicted_count, critique_passed,
                )
            except Exception as verify_err:
                logger.warning(f"[multi_agent] VerificationPipeline skipped: {verify_err}")

            trace.log_summary(success=True, extra={"stage": "multi_agent", "retrieval_coverage": agent_result.retrieval_coverage})
            t_end = _time.time()
            background_tasks.add_task(
                mm.save_turn, req.session_id, req.user_id or "anonymous",
                req.question, agent_result.answer
            )
            from VENUAI.analytics import get_analytics
            background_tasks.add_task(
                get_analytics().log_query,
                req.session_id, req.user_id or "anonymous", req.question,
                len(agent_result.answer), (t_end - t_start) * 1000,
                ma_ver.get("grounding_score") or 0.0, ma_ver.get("hallucination_score") or 0.0,
                agent_used, tools_called, None
            )
            return QueryResponse(
                answer=agent_result.answer,
                sources=[QuerySource(document=s.document, page=s.page,
                                     chunk_preview=s.chunk_preview)
                         for s in agent_result.sources],
                model=agent_result.agent_name,
                chunks_retrieved=len(agent_result.sources),
                grounding_score=ma_ver.get("grounding_score"),
                hallucination_score=ma_ver.get("hallucination_score"),
                verification_flags=ma_ver.get("verification_flags", []),
                annotated_answer=agent_result.answer,
                evidence_coverage=evidence_coverage,
                unsupported_claim_count=unsupported_count,
                contradicted_claim_count=contradicted_count,
                critique_passed=critique_passed,
                blocked=ma_safe["blocked"], block_reason=ma_safe["block_reason"],
                verified=ma_ver["verified"] and not ma_safe["blocked"],
                verification_reason=ma_ver.get("verification_reason"),
                safety_check=ma_safe["safety_check"],
                answer_basis=ANSWER_BASIS_DOCUMENTS if agent_result.documents else ANSWER_BASIS_NONE,
                trace_id=trace.request_id,
            )

        # Mode: ReAct Agent
        if req.agent_mode:
            from langgraph.prebuilt import create_react_agent
            from VENUAI.agent_tools import build_agent_tools
            # LangGraph's prebuilt agent needs a LangChain chat model, so this
            # path can't use AIGateway.complete; it is built via the central
            # helper (timeout/retry policy) on the router's first model, and
            # that model id is reported as the model used.
            from VENUAI.ai_gateway.model_router import TaskType as _TT, reasoning_effort_for as _eff
            react_model = _primary_model_label()
            from VENUAI.ai_gateway.model_registry import get_model as _get_model
            _entry = _get_model(react_model)
            _style = _entry.reasoning_style if _entry else ""
            agent_llm = get_chat_groq(
                react_model, temperature=0.2, max_tokens=3000,
                reasoning_effort=_eff(_TT.GENERATION) if _style == "effort" else None,
            )
            # Tools are built fresh per request, bound to this caller's identity
            # and document-visibility set — the ReAct agent can no longer
            # retrieve/list documents or recall memory outside this user's
            # authorization. See Documents/Backend/authentication.md.
            # collected_docs (Phase 5): every real Document drdo_kb_search
            # retrieves across the agent's tool calls, so this mode can
            # also get real sources and claim-level verification, not just
            # a free-text answer — see agent_tools.build_agent_tools's
            # docstring.
            collected_docs: List[Document] = []
            request_tools = build_agent_tools(
                user_id=current_user.id,
                allowed_doc_ids=get_visible_doc_ids(current_user),
                user_role=current_user.role,
                collected_docs=collected_docs,
            )
            react_agent = create_react_agent(agent_llm, request_tools)
            result = react_agent.invoke(
                {"messages": [{"role": "user", "content": req.question}]},
                config={"recursion_limit": 8},
            )
            final_msg = result["messages"][-1]
            answer_text = final_msg.content if hasattr(final_msg, "content") else str(final_msg)
            agent_used = "react_agent"

            react_sources: List[QuerySource] = []
            seen_react: set = set()
            for doc in collected_docs:
                m = doc.metadata
                key = (m.get("filename", "Unknown"), m.get("page", 0))
                if key not in seen_react:
                    seen_react.add(key)
                    react_sources.append(QuerySource(
                        document=m.get("filename", "Unknown Document"),
                        page=m.get("page", 0) + 1,
                        chunk_preview=doc.page_content[:120].strip() + "…",
                    ))

            # Always run verification and persist a record — even with zero
            # collected_docs, "nothing was verifiable for this answer" is
            # itself a real, honest fact worth persisting (same design
            # decision as standard RAG's KB-fallback path — see
            # Documents/AI/evidence-explorer.md Design Principle 1), not a
            # reason to skip.
            evidence_coverage, unsupported_count, contradicted_count, critique_passed = None, None, None, False
            from VENUAI import evidence_store
            from VENUAI.answer_pipeline import run_verification, apply_output_safety, ANSWER_BASIS_DOCUMENTS, ANSWER_BASIS_NONE
            react_ver = run_verification(answer_text, req.question, collected_docs, light=True)
            react_safe = apply_output_safety(react_ver.pop("final_answer"), react_ver.get("grounding_score"), user_id=current_user.id)
            answer_text = react_safe["answer"]
            try:
                from VENUAI.verification_pipeline import get_verification_pipeline
                pipeline_result = get_verification_pipeline().run(req.question, answer_text, collected_docs)
                evidence_coverage = pipeline_result.evidence_graph.evidence_coverage
                unsupported_count = pipeline_result.critique.unsupported_claim_count
                contradicted_count = pipeline_result.critique.contradicted_claim_count
                critique_passed = pipeline_result.critique.passed
                background_tasks.add_task(
                    evidence_store.save_record,
                    current_user.id, current_user.tenant_id, req.question, answer_text, "react_agent",
                    evidence_store.serialize_claims(pipeline_result.evidence_graph.claims),
                    evidence_coverage, unsupported_count, contradicted_count, critique_passed,
                )
            except Exception as verify_err:
                logger.warning(f"[react_agent] VerificationPipeline skipped: {verify_err}")

            trace.log_summary(success=True, extra={"stage": "react_agent"})
            for msg in result["messages"]:
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        tools_called.append(tc.get("name", ""))
            t_end = _time.time()
            background_tasks.add_task(
                mm.save_turn, req.session_id, req.user_id or "anonymous",
                req.question, answer_text
            )
            from VENUAI.analytics import get_analytics
            background_tasks.add_task(
                get_analytics().log_query,
                req.session_id, req.user_id or "anonymous", req.question,
                len(answer_text), (t_end - t_start) * 1000,
                react_ver.get("grounding_score") or 0.0, react_ver.get("hallucination_score") or 0.0,
                agent_used, tools_called, None
            )
            return QueryResponse(
                answer=answer_text,
                sources=react_sources,
                model=f"{react_model} (ReAct)",
                chunks_retrieved=len(collected_docs),
                grounding_score=react_ver.get("grounding_score"),
                hallucination_score=react_ver.get("hallucination_score"),
                verification_flags=react_ver.get("verification_flags", []),
                annotated_answer=answer_text,
                evidence_coverage=evidence_coverage,
                unsupported_claim_count=unsupported_count,
                contradicted_claim_count=contradicted_count,
                critique_passed=critique_passed,
                blocked=react_safe["blocked"], block_reason=react_safe["block_reason"],
                verified=react_ver["verified"] and not react_safe["blocked"],
                verification_reason=react_ver.get("verification_reason"),
                safety_check=react_safe["safety_check"],
                answer_basis=ANSWER_BASIS_DOCUMENTS if collected_docs else ANSWER_BASIS_NONE,
                trace_id=trace.request_id,
            )

        # Mode: Standard RAG (LangGraph)
        final_state = venuai_graph.invoke({
            "question":          req.question,
            "top_k":             req.top_k,
            "history":           effective_history,
            "documents":         [],
            "answer":            "",
            "sources":           [],
            "model_used":        "",
            "working_memory":    working_memory,
            "semantic_memories": semantic_memories,
            "grounding_score":   None,
            "hallucination_score": None,
            "verification_flags":  [],
            "annotated_answer":    "",
            "allowed_doc_ids":     get_visible_doc_ids(current_user),
            "evidence_coverage":   None,
            "unsupported_claim_count": None,
            "contradicted_claim_count": None,
            "critique_passed":     False,
            "evidence_claims":     [],
            "retrieval_query":     req.question,
            "retrieval_scores":    [],
            "insufficient_context": False,
            "user_id":             current_user.id,
        })
        t_end = _time.time()
        final_answer = final_state.get("answer", "No response generated.")

        background_tasks.add_task(
            mm.save_turn, req.session_id, req.user_id or "anonymous",
            req.question, final_answer
        )
        from VENUAI.evidence_store import save_record as _save_evidence_record
        background_tasks.add_task(
            _save_evidence_record,
            current_user.id, current_user.tenant_id, req.question, final_answer, "query",
            final_state.get("evidence_claims", []), final_state.get("evidence_coverage") or 0.0,
            final_state.get("unsupported_claim_count") or 0, final_state.get("contradicted_claim_count") or 0,
            final_state.get("critique_passed", False),
        )
        from VENUAI.analytics import get_analytics
        background_tasks.add_task(
            get_analytics().log_query,
            req.session_id, req.user_id or "anonymous", req.question,
            len(final_answer), (t_end - t_start) * 1000,
            final_state.get("grounding_score") or 0.0,
            final_state.get("hallucination_score") or 0.0,
            agent_used, tools_called, error_str
        )

        trace.log_summary(success=True, extra={
            "stage": "standard_rag", "chunks_retrieved": len(final_state.get("documents", [])),
            "grounding_score": final_state.get("grounding_score", 0.0),
            "critique_passed": final_state.get("critique_passed", False),
        })
        return QueryResponse(
            answer=final_answer,
            sources=final_state.get("sources", []),
            model=final_state.get("model_used", "LangGraph Workflow"),
            chunks_retrieved=len(final_state.get("documents", [])),
            grounding_score=final_state.get("grounding_score"),
            hallucination_score=final_state.get("hallucination_score"),
            verification_flags=final_state.get("verification_flags", []),
            annotated_answer=final_state.get("annotated_answer") or final_answer,
            evidence_coverage=final_state.get("evidence_coverage"),
            unsupported_claim_count=final_state.get("unsupported_claim_count"),
            contradicted_claim_count=final_state.get("contradicted_claim_count"),
            critique_passed=final_state.get("critique_passed", False),
            blocked=final_state.get("blocked", False),
            block_reason=final_state.get("block_reason"),
            verified=final_state.get("verified", False),
            verification_reason=final_state.get("verification_reason"),
            citations=final_state.get("citations", []),
            safety_check=final_state.get("safety_check"),
            answer_basis=final_state.get("answer_basis"),
            answer_basis_notice=(_kb_notice() if final_state.get("answer_basis") == "builtin_knowledge_base" else None),
            quarantined_chunks=final_state.get("quarantined", 0),
            trace_id=trace.request_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        # Error hygiene: full detail is logged server-side under a trace id; the client gets a
        # generic message plus that id (never str(e)).
        trace.log_summary(success=False, extra={"stage": "exception", "error_type": type(e).__name__})
        error_str = type(e).__name__
        logger.exception(f"Query execution error [trace_id={trace.request_id}]")
        raise HTTPException(
            status_code=500,
            detail=f"Query execution failed. Reference trace ID: {trace.request_id}",
            headers={"X-Trace-Id": trace.request_id},
        )


@app.post("/query/stream")
@limiter.limit("30/minute")
async def query_stream(
    request: Request,
    req: QueryRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Stream LLM tokens via Server-Sent Events."""
    import asyncio
    from fastapi.responses import StreamingResponse
    from VENUAI.context_engineer import ContextBudget, rank_chunks, compress_history, build_drdo_prompt
    from VENUAI.memory_manager import get_memory_manager

    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    req.user_id = current_user.id

    # Input guardrails (same engine as /query). A block emits ONE SSE event and ends.
    from VENUAI.guardrails import get_guardrail_engine
    from VENUAI.observability import new_request_id
    trace_id = new_request_id()
    g_res = await asyncio.get_running_loop().run_in_executor(
        None, lambda: get_guardrail_engine().validate_input(req.question, user_id=req.user_id or "anonymous")
    )
    if not g_res.passed:
        blocked_event = {
            "blocked": True,
            "block_reason": g_res.block_reason or g_res.violation_type,
            "violation_type": g_res.violation_type,
            "answer": g_res.sanitized_input_or_output,
            "done": True,
            "sources": [],
            "citations": [],
            "grounding_score": None,
            "hallucination_score": None,
            "verified": False,
            "verification_reason": "request blocked by input guardrails",
            "trace_id": trace_id,
        }

        async def blocked_generator():
            yield f"data: {json.dumps(blocked_event)}\n\n"

        return StreamingResponse(
            blocked_generator(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Trace-Id": trace_id},
        )

    async def event_generator():
        try:
            mm = get_memory_manager()
            mem_ctx = mm.get_context(req.session_id, req.user_id or "anonymous", req.question)
            effective_history = req.history or mem_ctx.get("working", [])

            allowed_doc_ids = get_visible_doc_ids(current_user)
            insufficient = False
            docs = []
            if load_metadata():
                # Same shared pipeline as /query (rewrite -> dense+BM25 -> RRF -> rerank ->
                # relevance floor, multimodal regions included); runs in a worker thread.
                from VENUAI.retrieval import retrieve_documents
                _hist = effective_history
                ret = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: retrieve_documents(
                        get_vector_store(), req.question, req.top_k, allowed_doc_ids, history=_hist,
                    )
                )
                docs = ret.documents
                insufficient = ret.insufficient_context
            if insufficient:
                done_event = {
                    "done": True, "sources": [], "answer": ABSTAIN_ANSWER, "citations": [],
                    "grounding_score": None, "hallucination_score": None,
                    "verification_flags": ["INSUFFICIENT_CONTEXT"],
                    "verified": False, "verification_reason": "insufficient_context",
                    "evidence_coverage": None, "unsupported_claim_count": None,
                    "contradicted_claim_count": None, "critique_passed": False,
                    "safety_check": None, "blocked": False, "block_reason": None,
                    "answer_basis": "none", "answer_basis_notice": None,
                    "quarantined_chunks": 0, "insufficient_context": True,
                    "model": "retrieval-abstain (no LLM call)", "trace_id": trace_id,
                }
                yield f'data: {json.dumps({"model": done_event["model"]})}\n\n'
                yield f"data: {json.dumps(done_event)}\n\n"
                return

            ranked_docs = rank_chunks(docs, req.question)
            answer_basis = "uploaded_documents" if ranked_docs else "none"
            if not ranked_docs:
                ranked_docs = _kb_fallback_docs()
                if ranked_docs:
                    answer_basis = "builtin_knowledge_base"

            prompt_info: dict = {}
            prompt_text = build_drdo_prompt(
                question=req.question,
                ranked_docs=ranked_docs,
                compressed_history=compress_history(effective_history),
                budget=ContextBudget(),
                semantic_memories=mem_ctx.get("semantic", []),
                info=prompt_info,
            )
            prompt_docs = prompt_info.get("used_docs", [])
            from VENUAI.grounded_citations import CitationStreamFilter
            cite_filter = CitationStreamFilter()

            from VENUAI.ai_gateway.gateway import get_gateway
            from VENUAI.ai_gateway.model_router import TaskType as _StreamTT
            full_answer = ""
            # Gateway streaming: fallback chain + circuit breaker before the
            # first token; sync Groq iteration runs in worker threads.
            gw_stream = await get_gateway().astream(
                _StreamTT.GENERATION,
                messages=[{"role": "user", "content": prompt_text}],
                max_tokens=1500,
                temperature=0.2,
            )
            stream_model_used = gw_stream.model_used
            yield f'data: {{"model": {json.dumps(stream_model_used)}}}\n\n'
            async for delta in gw_stream:
                if delta:
                    full_answer += delta
                    visible = cite_filter.feed(delta)   # hides the trailing <citations> block
                    if visible:
                        yield f'data: {{"token": {json.dumps(visible)}}}\n\n'
            tail = cite_filter.flush()
            if tail:
                yield f'data: {{"token": {json.dumps(tail)}}}\n\n'

            sources_list = [
                {"document": q.document, "page": q.page, "chunk_preview": q.chunk_preview}
                for q in _sources_from_docs(prompt_docs)
            ]

            # Real verification after the final chunk (no hardcoded score): citations, grounding,
            # output guardrails. Runs in a worker thread (blocking LLM/NLI calls).
            post = await asyncio.get_running_loop().run_in_executor(
                None, lambda: _postprocess_answer(
                    full_answer, req.question, prompt_docs, req.user_id or "user", answer_basis,
                )
            )
            done_event = {
                "done": True,
                "sources": sources_list,
                "answer": post["answer"],            # final, citation-block-free (replaces streamed text)
                "citations": post["citations"],
                "grounding_score": post.get("grounding_score"),
                "hallucination_score": post.get("hallucination_score"),
                "verification_flags": post.get("verification_flags", []),
                "verified": post.get("verified", False),
                "verification_reason": post.get("verification_reason"),
                "evidence_coverage": post.get("evidence_coverage"),
                "unsupported_claim_count": post.get("unsupported_claim_count"),
                "contradicted_claim_count": post.get("contradicted_claim_count"),
                "critique_passed": post.get("critique_passed", False),
                "safety_check": post.get("safety_check"),
                "blocked": post.get("blocked", False),
                "block_reason": post.get("block_reason"),
                "answer_basis": answer_basis,
                "answer_basis_notice": _kb_notice() if answer_basis == "builtin_knowledge_base" else None,
                "quarantined_chunks": prompt_info.get("quarantined", 0),
                "trace_id": trace_id,
            }
            yield f"data: {json.dumps(done_event)}\n\n"

            await asyncio.get_event_loop().run_in_executor(
                None, lambda: mm.save_turn(
                    req.session_id, req.user_id or "anonymous", req.question, post["answer"]
                )
            )
        except Exception as e:
            # Error hygiene: detail goes to the log under the trace id, not to the client.
            logger.exception(f"[stream] error [trace_id={trace_id}]")
            yield f"data: {json.dumps({'error': f'Stream failed. Reference trace ID: {trace_id}', 'trace_id': trace_id})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Trace-Id": trace_id},
    )


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
    bump_corpus_version()
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
        raise HTTPException(status_code=422, detail=f"Failed to process PDF for multimodal extraction: {e}")

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
    uvicorn.run("rag_server:app", host="0.0.0.0", port=8000, reload=False)
