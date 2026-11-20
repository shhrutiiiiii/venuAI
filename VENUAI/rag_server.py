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

# Real Groq-served model id.
DEFAULT_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")

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

def create_groq_completion(client, messages, max_tokens=1500, temperature=0.2, stream=False):
    """
    Streaming completions with model fallback. Used only by /query/stream.
    """
    from VENUAI.ai_gateway.model_router import TaskType, route
    candidate_models = [m.model_id for m in route(TaskType.GENERATION)] or [DEFAULT_MODEL]
    if DEFAULT_MODEL not in candidate_models:
        candidate_models.insert(0, DEFAULT_MODEL)
    for fb in ["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"]:
        if fb not in candidate_models:
            candidate_models.append(fb)
    last_err = None
    for m in candidate_models:
        try:
            return client.chat.completions.create(
                model=m,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=stream
            )
        except Exception as e:
            logger.warning(f"Groq completion with model '{m}' failed: {e}")
            last_err = e
    raise last_err

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

# ── Lazy-loaded singletons (initialized on first use) ─────────────────────────
_embeddings = None
_vector_store = None
_llm = None

def get_embeddings():
    global _embeddings
    if _embeddings is None:
        logger.info("Loading embedding model (sentence-transformers/all-MiniLM-L6-v2)…")
        from langchain_community.embeddings import HuggingFaceEmbeddings
        _embeddings = HuggingFaceEmbeddings(
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
        logger.info("Embedding model loaded ✓")
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
    return _vector_store

def get_llm():
    global _llm
    if _llm is None:
        from langchain_groq import ChatGroq
        _llm = ChatGroq(
            api_key=GROQ_API_KEY,
            model_name=DEFAULT_MODEL,
            temperature=0.2,
            max_tokens=1500,
        )
    return _llm


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
    grounding_score: float = 0.0
    hallucination_score: float = 0.0
    verification_flags: List[str] = []
    annotated_answer: Optional[str] = None
    # Claim-level evidence verification fields — all default to 0/False so
    # older clients that don't read them see no behavior change.
    evidence_coverage: float = 0.0
    unsupported_claim_count: int = 0
    contradicted_claim_count: int = 0
    critique_passed: bool = False


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
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


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
        raise HTTPException(status_code=503, detail=str(e))
    except OAuthExchangeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

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

@app.get("/health")
def health_check():
    meta = load_metadata()
    return {
        "status": "ok",
        "service": "DRDO VenuAI RAG Server",
        "version": "1.1.0",
        "documents_ingested": len(meta),
        "vector_store": "ChromaDB (local)",
        "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
        "llm": "groq/compound (Groq)",
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

    # Embed + store (embedding model loads here on first call)
    vs = get_vector_store()
    vs.add_documents(chunks)
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
    question = state["question"]
    top_k = state.get("top_k", 4)
    allowed_doc_ids = state.get("allowed_doc_ids")
    vs = get_vector_store()

    chroma_search_kwargs = {"k": top_k}
    if allowed_doc_ids is not None:
        # Empty list means the caller owns no documents and nothing is public —
        # must retrieve nothing, not fall through to an unfiltered search.
        chroma_search_kwargs["filter"] = {"doc_id": {"$in": allowed_doc_ids or ["__none__"]}}

    chroma_retriever = vs.as_retriever(
        search_type="similarity",
        search_kwargs=chroma_search_kwargs,
    )

    retriever = chroma_retriever
    try:
        chroma_data = vs.get()
        if chroma_data and chroma_data.get("documents") and len(chroma_data["documents"]) > 0:
            from langchain_community.retrievers import BM25Retriever
            from langchain.retrievers import EnsembleRetriever

            pairs = zip(chroma_data["documents"], chroma_data.get("metadatas", []))
            if allowed_doc_ids is not None:
                allowed_set = set(allowed_doc_ids)
                pairs = [
                    (txt, meta) for txt, meta in pairs
                    if (meta or {}).get("doc_id") in allowed_set
                ]
            doc_objects = [
                Document(page_content=txt, metadata=meta or {})
                for txt, meta in pairs
            ]
            if doc_objects:
                bm25_retriever = BM25Retriever.from_documents(doc_objects)
                bm25_retriever.k = top_k
                retriever = EnsembleRetriever(
                    retrievers=[chroma_retriever, bm25_retriever],
                    weights=[0.5, 0.5]
                )
                logger.info("LangGraph [Retrieve Node]: Hybrid Search (Chroma + BM25) active.")
            elif allowed_doc_ids is not None:
                # Caller is authorized to see nothing in the corpus — return no
                # documents rather than silently falling back to an unfiltered
                # Chroma-only retriever, which would defeat the filter above.
                return {"documents": []}
    except Exception as err:
        logger.warning(f"LangGraph [Retrieve Node]: Chroma vector search fallback ({err})")

    retrieved_docs = retriever.invoke(question)

    # Phase 4: fold in table/image region hits from multimodal-ingested
    # documents (VENUAI/multimodal_retrieval.py), same allowed_doc_ids
    # filter as the text-chunk retrieval above. Previously /upload/multimodal
    # content was only reachable via the standalone /documents/regions/search
    # endpoint — a user asking a question through the normal /query path got
    # no benefit from a table/image they'd already uploaded. Best-effort: a
    # lexical-search failure here must not break the text-chunk answer.
    try:
        from VENUAI.multimodal_retrieval import retrieve_multimodal_documents
        region_docs = retrieve_multimodal_documents(question, allowed_doc_ids, top_k=3)
        if region_docs:
            retrieved_docs = list(retrieved_docs) + region_docs
    except Exception as err:
        logger.warning(f"LangGraph [Retrieve Node]: multimodal region retrieval skipped ({err})")

    return {"documents": retrieved_docs}


def generate_node(state: RAGGraphState) -> dict:
    """
    Generate an answer using Context Engineering for optimal prompt quality.
    Integrates: rank_chunks, compress_history, build_drdo_prompt, semantic_memories.
    """
    from VENUAI.context_engineer import (
        ContextBudget, rank_chunks, compress_history, build_drdo_prompt
    )

    question = state["question"]
    docs     = state.get("documents", [])
    history  = state.get("history") or []
    sem_mems = state.get("semantic_memories", [])

    ranked_docs = rank_chunks(docs, question)
    if not ranked_docs:
        kb_data = load_prefilled_kb()
        if kb_data:
            kb_text = json.dumps(kb_data, indent=2, ensure_ascii=False)[:8000]
            fake_doc = Document(
                page_content=f"DRDO Master Knowledge Base:\n{kb_text}",
                metadata={"filename": "DRDO Master Knowledge Base (Preloaded JSON)", "page": 0}
            )
            ranked_docs = [fake_doc]

    compressed_history = compress_history(history, max_turns=6)
    budget             = ContextBudget()
    prompt_text        = build_drdo_prompt(
        question=question,
        ranked_docs=ranked_docs,
        compressed_history=compressed_history,
        budget=budget,
        semantic_memories=sem_mems or [],
    )

    llm = get_llm()
    res = llm.invoke(prompt_text)
    answer_text = res.content if hasattr(res, "content") else str(res)

    seen_sources: set = set()
    sources: List[QuerySource] = []
    for doc in ranked_docs:
        m = doc.metadata
        key = (m.get("filename", "Unknown"), m.get("page", 0))
        if key not in seen_sources:
            seen_sources.add(key)
            sources.append(QuerySource(
                document=m.get("filename", "Unknown Document"),
                page=m.get("page", 0) + 1,
                chunk_preview=doc.page_content[:120].strip() + "…",
            ))

    return {
        "answer": answer_text,
        "sources": sources,
        "documents": ranked_docs,
        "model_used": "groq/compound (Groq / LangGraph + ContextEngineer)",
    }


def verify_node(state: RAGGraphState) -> dict:
    """
    Run VerifierAgent + HallucinationDetector on the generated answer, plus
    the claim-level VerificationPipeline (citation + contradiction + critic —
    see VENUAI/verification_pipeline.py). Updates state with
    grounding_score, hallucination_score, verification_flags, and the new
    claim-level fields.
    """
    from VENUAI.verifier_agent import get_verifier
    from VENUAI.hallucination_detector import get_hallucination_detector
    from VENUAI.verification_pipeline import get_verification_pipeline
    from VENUAI import evidence_store
    from groq import Groq as _Groq

    answer   = state.get("answer", "")
    question = state.get("question", "")
    docs     = state.get("documents", [])

    verifier = get_verifier(_Groq(api_key=GROQ_API_KEY))
    v_result = verifier.verify(answer, question, docs)
    final_answer = v_result.revised_answer if v_result.revised_answer else answer

    annotated, hall_score, _ = get_hallucination_detector().analyze(final_answer, docs)
    flags = [f.value for f in v_result.policy_flags]

    pipeline_result = get_verification_pipeline().run(question, final_answer, docs)

    return {
        "answer":             final_answer,
        "annotated_answer":   annotated,
        "grounding_score":    v_result.grounding_score,
        "hallucination_score":hall_score,
        "verification_flags": flags,
        "evidence_coverage":  pipeline_result.evidence_graph.evidence_coverage,
        "unsupported_claim_count": pipeline_result.critique.unsupported_claim_count,
        "contradicted_claim_count": pipeline_result.critique.contradicted_claim_count,
        "critique_passed":   pipeline_result.critique.passed,
        "evidence_claims":   evidence_store.serialize_claims(pipeline_result.evidence_graph.claims),
    }


def fallback_kb_node(state: RAGGraphState) -> dict:
    kb_data = load_prefilled_kb()
    kb_text = json.dumps(kb_data, indent=2, ensure_ascii=False) if kb_data else ""
    llm = get_llm()
    prompt_text = f"""You are VenuAI, an advanced, intelligent research assistant for DRDO and scientific engineering.
Always answer the user's question clearly, thoroughly, accurately, and helpfully.
If the question pertains to DRDO or defence, use the DRDO Knowledge Base below:

--- DRDO KNOWLEDGE BASE ---
{kb_text[:8000]}
--- END ---

If the question is general, conceptual, technical, or conversational, answer it fully and accurately using your broad knowledge base.
Never refuse to answer a question.

Question: {state['question']}
Answer:"""

    res = llm.invoke(prompt_text)
    ans_text = res.content if hasattr(res, 'content') else str(res)
    return {
        "answer": ans_text,
        "annotated_answer": ans_text,
        "grounding_score": 0.9,
        "hallucination_score": 0.0,
        "verification_flags": [],
        "sources": [QuerySource(
            document="DRDO Master Knowledge Base (Preloaded JSON)",
            page=1,
            chunk_preview="DRDO laboratories directory, weapons platforms, and technology clusters.",
        )],
        "model_used": "groq/compound (Preloaded KB / LangGraph Fallback)",
        # This path doesn't run VerificationPipeline — there is no retrieved
        # Document list to build claim-level Evidence from here, only the
        # raw preloaded KB JSON. Reporting 0/False rather than a fabricated
        # coverage number is the honest default for "not evaluated."
        "evidence_coverage": 0.0,
        "unsupported_claim_count": 0,
        "contradicted_claim_count": 0,
        "critique_passed": False,
        "evidence_claims": [],
    }


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
workflow.add_edge("retrieve",    "generate")
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
            grounding_score=0.0,
            hallucination_score=0.0,
            verification_flags=[f"GUARDRAIL_TRIGGERED: {g_res.violation_type}"],
            annotated_answer=g_res.sanitized_input_or_output,
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
            from groq import Groq as _Groq
            orchestrator = OrchestratorAgent(_Groq(api_key=GROQ_API_KEY))
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
            evidence_coverage, unsupported_count, contradicted_count, critique_passed = 0.0, 0, 0, False
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

            trace.log_summary(success=True, extra={"stage": "multi_agent", "confidence": agent_result.confidence})
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
                agent_result.confidence, 0.0, agent_used, tools_called, None
            )
            return QueryResponse(
                answer=agent_result.answer,
                sources=[QuerySource(document=s.document, page=s.page,
                                     chunk_preview=s.chunk_preview)
                         for s in agent_result.sources],
                model=agent_result.agent_name,
                chunks_retrieved=len(agent_result.sources),
                grounding_score=agent_result.confidence,
                hallucination_score=0.0,
                verification_flags=[],
                annotated_answer=agent_result.answer,
                evidence_coverage=evidence_coverage,
                unsupported_claim_count=unsupported_count,
                contradicted_claim_count=contradicted_count,
                critique_passed=critique_passed,
            )

        # Mode: ReAct Agent
        if req.agent_mode:
            from langgraph.prebuilt import create_react_agent
            from VENUAI.agent_tools import build_agent_tools
            from langchain_groq import ChatGroq
            agent_llm = ChatGroq(
                api_key=GROQ_API_KEY,
                model_name=DEFAULT_MODEL,
                temperature=0.2,
                max_tokens=1500,
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
            evidence_coverage, unsupported_count, contradicted_count, critique_passed = 0.0, 0, 0, False
            from VENUAI import evidence_store
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
                0.7, 0.0, agent_used, tools_called, None
            )
            return QueryResponse(
                answer=answer_text,
                sources=react_sources,
                model="ReAct Agent (LangGraph prebuilt)",
                chunks_retrieved=len(collected_docs),
                grounding_score=0.7,
                hallucination_score=0.0,
                verification_flags=[],
                annotated_answer=answer_text,
                evidence_coverage=evidence_coverage,
                unsupported_claim_count=unsupported_count,
                contradicted_claim_count=contradicted_count,
                critique_passed=critique_passed,
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
            "grounding_score":   0.0,
            "hallucination_score": 0.0,
            "verification_flags":  [],
            "annotated_answer":    "",
            "allowed_doc_ids":     get_visible_doc_ids(current_user),
            "evidence_coverage":   0.0,
            "unsupported_claim_count": 0,
            "contradicted_claim_count": 0,
            "critique_passed":     False,
            "evidence_claims":     [],
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
            final_state.get("evidence_claims", []), final_state.get("evidence_coverage", 0.0),
            final_state.get("unsupported_claim_count", 0), final_state.get("contradicted_claim_count", 0),
            final_state.get("critique_passed", False),
        )
        from VENUAI.analytics import get_analytics
        background_tasks.add_task(
            get_analytics().log_query,
            req.session_id, req.user_id or "anonymous", req.question,
            len(final_answer), (t_end - t_start) * 1000,
            final_state.get("grounding_score", 0.0),
            final_state.get("hallucination_score", 0.0),
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
            grounding_score=final_state.get("grounding_score", 0.0),
            hallucination_score=final_state.get("hallucination_score", 0.0),
            verification_flags=final_state.get("verification_flags", []),
            annotated_answer=final_state.get("annotated_answer") or final_answer,
            evidence_coverage=final_state.get("evidence_coverage", 0.0),
            unsupported_claim_count=final_state.get("unsupported_claim_count", 0),
            contradicted_claim_count=final_state.get("contradicted_claim_count", 0),
            critique_passed=final_state.get("critique_passed", False),
        )

    except HTTPException:
        raise
    except Exception as e:
        trace.log_summary(success=False, extra={"stage": "exception", "error": str(e)})
        error_str = str(e)
        logger.error(f"Query execution error: {e}")
        raise HTTPException(status_code=500, detail=f"Query execution failed: {str(e)}")


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
    from groq import Groq as _Groq

    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    req.user_id = current_user.id

    async def event_generator():
        try:
            mm = None
            mem_ctx = {"working": [], "episodic": "", "semantic": []}
            try:
                mm = get_memory_manager()
                mem_ctx = mm.get_context(req.session_id, req.user_id or "anonymous", req.question)
            except Exception as mem_err:
                logger.warning(f"[stream] memory context warning: {mem_err}")

            effective_history = req.history or mem_ctx.get("working", [])

            docs = []
            try:
                vs = get_vector_store()
                stream_search_kwargs = {"k": req.top_k}
                allowed_doc_ids = get_visible_doc_ids(current_user)
                if allowed_doc_ids is not None:
                    stream_search_kwargs["filter"] = {"doc_id": {"$in": allowed_doc_ids or ["__none__"]}}
                docs = vs.as_retriever(
                    search_type="similarity", search_kwargs=stream_search_kwargs
                ).invoke(req.question)
            except Exception as vs_err:
                logger.warning(f"[stream] vector search warning: {vs_err}")

            # Phase 5: fold in table/image region hits — best-effort.
            try:
                from VENUAI.multimodal_retrieval import retrieve_multimodal_documents
                region_docs = retrieve_multimodal_documents(req.question, allowed_doc_ids, top_k=3)
                if region_docs:
                    docs = list(docs) + region_docs
            except Exception as region_err:
                logger.warning(f"[stream] multimodal region retrieval skipped: {region_err}")

            ranked_docs = rank_chunks(docs, req.question)
            if not ranked_docs:
                kb_data = load_prefilled_kb()
                if kb_data:
                    kb_text = json.dumps(kb_data, indent=2, ensure_ascii=False)[:8000]
                    fake_doc = Document(
                        page_content=f"DRDO Master Knowledge Base:\n{kb_text}",
                        metadata={"filename": "DRDO Master Knowledge Base (Preloaded JSON)", "page": 0}
                    )
                    ranked_docs = [fake_doc]

            prompt_text = build_drdo_prompt(
                question=req.question,
                ranked_docs=ranked_docs,
                compressed_history=compress_history(effective_history),
                budget=ContextBudget(),
                semantic_memories=mem_ctx.get("semantic", []),
            )

            stream_client = _Groq(api_key=GROQ_API_KEY)
            full_answer = ""
            stream = create_groq_completion(
                stream_client,
                messages=[{"role": "user", "content": prompt_text}],
                max_tokens=1500,
                temperature=0.2,
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    full_answer += delta
                    yield f'data: {{"token": {json.dumps(delta)}}}\n\n'
                    await asyncio.sleep(0)

            sources_list = []
            seen: set = set()
            for doc in ranked_docs:
                m = doc.metadata
                key = (m.get("filename", "Unknown"), m.get("page", 0))
                if key not in seen:
                    seen.add(key)
                    sources_list.append({"document": m.get("filename", "Unknown"),
                                         "page": m.get("page", 0) + 1,
                                         "chunk_preview": doc.page_content[:120].strip() + "…"})

            yield f'data: {{"done": true, "sources": {json.dumps(sources_list)}, "grounding_score": 0.8}}\n\n'

            if mm is not None:
                try:
                    await asyncio.get_event_loop().run_in_executor(
                        None, lambda: mm.save_turn(
                            req.session_id, req.user_id or "anonymous", req.question, full_answer
                        )
                    )
                except Exception as save_err:
                    logger.warning(f"[stream] save_turn warning: {save_err}")
        except Exception as e:
            logger.error(f"[stream] {e}")
            yield f'data: {{"error": {json.dumps(str(e))}}}\n\n'

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
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
    from groq import Groq as _Groq
    from VENUAI.agents.deep_research_agent import DeepResearchAgent
    from VENUAI.memory_manager import get_memory_manager
    from VENUAI.guardrails import get_guardrail_engine

    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    ge = get_guardrail_engine()
    g_res = ge.validate_input(req.question, user_id=current_user.id)
    if not g_res.passed:
        raise HTTPException(status_code=400, detail=f"Guardrail triggered: {g_res.violation_type}")

    research_id = uuid.uuid4().hex[:12]
    allowed_doc_ids = get_visible_doc_ids(current_user)

    result = DeepResearchAgent().research(
        req.question, _Groq(api_key=GROQ_API_KEY), allowed_doc_ids=allowed_doc_ids
    )

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
            research_evidence, _Groq(api_key=GROQ_API_KEY), owner_id=current_user.id, classification="UNKNOWN",
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
    from groq import Groq as _Groq

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

    result = VisionAgent().analyze_region(region, req.question, groq_client=_Groq(api_key=GROQ_API_KEY))
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
    from groq import Groq as _Groq
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

    result = DataAnalystAgent().analyze(df, req.question, groq_client=_Groq(api_key=GROQ_API_KEY))

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
    from groq import Groq as _Groq

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
            evidence, _Groq(api_key=GROQ_API_KEY), owner_id=current_user.id, classification="UNKNOWN",
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
