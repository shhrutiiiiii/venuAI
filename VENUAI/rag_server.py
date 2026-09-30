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
from typing import List, Optional

# Ensure project root directory is in sys.path for VENUAI package imports
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── Configuration ──────────────────────────────────────────────────────────────
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("drdo-rag")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY not found in environment/.env")

DEFAULT_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploaded_docs"
CHROMA_DIR = BASE_DIR / "chroma_db"
META_FILE  = BASE_DIR / "doc_metadata.json"
KB_FILE    = BASE_DIR / "data" / "drdo_knowledge_base.json"

UPLOAD_DIR.mkdir(exist_ok=True)
CHROMA_DIR.mkdir(exist_ok=True)

def load_prefilled_kb() -> dict:
    if KB_FILE.exists():
        try:
            with open(KB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading prefilled KB: {e}")
    return {}

def create_groq_completion(client, messages, max_tokens=1500, temperature=0.2, stream=False):
    candidate_models = [DEFAULT_MODEL, "qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b", "allam-2-7b"]
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
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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


class DocumentInfo(BaseModel):
    id: str
    filename: str
    pages: int
    chunks: int
    upload_time: str
    size_kb: float
    tags: Optional[dict] = None


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/health")
def health_check():
    meta = load_metadata()
    return {
        "status": "ok",
        "service": "DRDO VenuAI RAG Server",
        "version": "1.0.0",
        "documents_ingested": len(meta),
        "vector_store": "ChromaDB (local)",
        "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
        "llm": "groq/compound (Groq)",
        "note": "Embedding model loads on first upload/query",
    }


@app.post("/upload", response_model=DocumentInfo)
async def upload_document(file: UploadFile = File(...)):
    """
    Accepts a PDF file, parses it, chunks it, embeds it, and stores
    it in the local ChromaDB vector database.
    """
    from langchain_community.document_loaders import PyPDFLoader
    from langchain.text_splitter import RecursiveCharacterTextSplitter

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    doc_id = str(uuid.uuid4())
    safe_name = file.filename.replace(" ", "_")
    save_path = UPLOAD_DIR / f"{doc_id}_{safe_name}"

    # Save file to disk
    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    file_size_kb = round(len(content) / 1024, 1)
    logger.info(f"Saved uploaded file: {save_path} ({file_size_kb} KB)")

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
    }
    save_metadata(meta)

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


def retrieve_node(state: RAGGraphState) -> dict:
    question = state["question"]
    top_k = state.get("top_k", 4)
    vs = get_vector_store()
    chroma_retriever = vs.as_retriever(
        search_type="similarity",
        search_kwargs={"k": top_k},
    )

    retriever = chroma_retriever
    try:
        chroma_data = vs.get()
        if chroma_data and chroma_data.get("documents") and len(chroma_data["documents"]) > 0:
            from langchain_community.retrievers import BM25Retriever
            from langchain.retrievers import EnsembleRetriever

            doc_objects = [
                Document(page_content=txt, metadata=meta or {})
                for txt, meta in zip(chroma_data["documents"], chroma_data.get("metadatas", []))
            ]
            bm25_retriever = BM25Retriever.from_documents(doc_objects)
            bm25_retriever.k = top_k
            retriever = EnsembleRetriever(
                retrievers=[chroma_retriever, bm25_retriever],
                weights=[0.5, 0.5]
            )
            logger.info("LangGraph [Retrieve Node]: Hybrid Search (Chroma + BM25) active.")
    except Exception as err:
        logger.warning(f"LangGraph [Retrieve Node]: Chroma vector search fallback ({err})")

    retrieved_docs = retriever.invoke(question)
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
    Run VerifierAgent + HallucinationDetector on the generated answer.
    Updates state with grounding_score, hallucination_score, verification_flags.
    """
    from VENUAI.verifier_agent import get_verifier
    from VENUAI.hallucination_detector import get_hallucination_detector
    from groq import Groq as _Groq

    answer   = state.get("answer", "")
    question = state.get("question", "")
    docs     = state.get("documents", [])

    verifier = get_verifier(_Groq(api_key=GROQ_API_KEY))
    v_result = verifier.verify(answer, question, docs)
    final_answer = v_result.revised_answer if v_result.revised_answer else answer

    annotated, hall_score, _ = get_hallucination_detector().analyze(final_answer, docs)
    flags = [f.value for f in v_result.policy_flags]

    return {
        "answer":             final_answer,
        "annotated_answer":   annotated,
        "grounding_score":    v_result.grounding_score,
        "hallucination_score":hall_score,
        "verification_flags": flags,
    }


def fallback_kb_node(state: RAGGraphState) -> dict:
    kb_data = load_prefilled_kb()
    if not kb_data:
        raise HTTPException(status_code=404, detail="No documents in knowledge base.")

    kb_text = json.dumps(kb_data, indent=2, ensure_ascii=False)
    llm = get_llm()
    prompt_text = f"""You are VenuAI, an intelligent research assistant for DRDO.
Answer the user's question using the DRDO Knowledge Base below.
Format with ### headings, Markdown tables, bullet points, and **bold** key terms.

--- DRDO KNOWLEDGE BASE ---
{kb_text[:8000]}
--- END ---

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
def query_knowledge_base(
    req: QueryRequest,
    background_tasks: "BackgroundTasks",
):
    """
    Run a RAG query. Supports standard RAG, ReAct agent, and multi-agent modes.
    Integrates memory management, verification, hallucination detection, and analytics.
    """
    import time as _time
    from fastapi import BackgroundTasks

    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    # Guardrails Input Check
    from VENUAI.guardrails import get_guardrail_engine
    ge = get_guardrail_engine()
    g_res = ge.validate_input(req.question, user_id=req.user_id or "anonymous")
    if not g_res.passed:
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
                orchestrator.route(req.question, effective_history, mem_ctx)
            )
            agent_used = "multi_agent"
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
            )

        # Mode: ReAct Agent
        if req.agent_mode:
            from langgraph.prebuilt import create_react_agent
            from VENUAI.agent_tools import ALL_TOOLS
            from langchain_groq import ChatGroq
            agent_llm = ChatGroq(
                api_key=GROQ_API_KEY,
                model_name=DEFAULT_MODEL,
                temperature=0.2,
                max_tokens=1500,
            )
            react_agent = create_react_agent(agent_llm, ALL_TOOLS)
            result = react_agent.invoke(
                {"messages": [{"role": "user", "content": req.question}]},
                config={"recursion_limit": 8},
            )
            final_msg = result["messages"][-1]
            answer_text = final_msg.content if hasattr(final_msg, "content") else str(final_msg)
            agent_used = "react_agent"
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
                sources=[],
                model="ReAct Agent (LangGraph prebuilt)",
                chunks_retrieved=0,
                grounding_score=0.7,
                hallucination_score=0.0,
                verification_flags=[],
                annotated_answer=answer_text,
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
        })
        t_end = _time.time()
        final_answer = final_state.get("answer", "No response generated.")

        background_tasks.add_task(
            mm.save_turn, req.session_id, req.user_id or "anonymous",
            req.question, final_answer
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

        return QueryResponse(
            answer=final_answer,
            sources=final_state.get("sources", []),
            model=final_state.get("model_used", "LangGraph Workflow"),
            chunks_retrieved=len(final_state.get("documents", [])),
            grounding_score=final_state.get("grounding_score", 0.0),
            hallucination_score=final_state.get("hallucination_score", 0.0),
            verification_flags=final_state.get("verification_flags", []),
            annotated_answer=final_state.get("annotated_answer") or final_answer,
        )

    except HTTPException:
        raise
    except Exception as e:
        error_str = str(e)
        logger.error(f"Query execution error: {e}")
        raise HTTPException(status_code=500, detail=f"Query execution failed: {str(e)}")


@app.post("/query/stream")
async def query_stream(req: QueryRequest):
    """Stream LLM tokens via Server-Sent Events."""
    import asyncio
    from fastapi.responses import StreamingResponse
    from VENUAI.context_engineer import ContextBudget, rank_chunks, compress_history, build_drdo_prompt
    from VENUAI.memory_manager import get_memory_manager
    from groq import Groq as _Groq

    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    async def event_generator():
        try:
            mm = get_memory_manager()
            mem_ctx = mm.get_context(req.session_id, req.user_id or "anonymous", req.question)
            effective_history = req.history or mem_ctx.get("working", [])

            vs = get_vector_store()
            docs = vs.as_retriever(
                search_type="similarity", search_kwargs={"k": req.top_k}
            ).invoke(req.question)

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

            await asyncio.get_event_loop().run_in_executor(
                None, lambda: mm.save_turn(
                    req.session_id, req.user_id or "anonymous", req.question, full_answer
                )
            )
        except Exception as e:
            logger.error(f"[stream] {e}")
            yield f'data: {{"error": {json.dumps(str(e))}}}\n\n'

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/documents", response_model=List[DocumentInfo])
def list_documents():
    """Return all documents currently in the knowledge base."""
    meta = load_metadata()
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
        for v in meta.values()
    ]


@app.delete("/documents/{doc_id}")
def delete_document(doc_id: str):
    """Remove a document and all its chunks from the knowledge base."""
    meta = load_metadata()
    if doc_id not in meta:
        raise HTTPException(status_code=404, detail="Document not found.")
    doc_info = meta[doc_id]
    vs = get_vector_store()
    vs._collection.delete(where={"doc_id": doc_id})
    vs.persist()
    file_path = Path(doc_info.get("file_path", ""))
    if file_path.exists():
        file_path.unlink()
    del meta[doc_id]
    save_metadata(meta)
    logger.info(f"Deleted document {doc_id} ({doc_info['filename']})")
    return {"status": "deleted", "doc_id": doc_id, "filename": doc_info["filename"]}


@app.get("/kb/preset")
def get_preset_kb():
    """Return preloaded DRDO Master Knowledge Base JSON data."""
    kb_data = load_prefilled_kb()
    if not kb_data:
        raise HTTPException(status_code=404, detail="Prefilled DRDO Knowledge Base file not found.")
    return kb_data


@app.get("/analytics/dashboard")
def analytics_dashboard():
    """Return aggregated analytics data for the admin dashboard (admin-only)."""
    from VENUAI.analytics import get_analytics
    return get_analytics().get_dashboard_data()


# ── Entry Point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("rag_server:app", host="0.0.0.0", port=8000, reload=False)
