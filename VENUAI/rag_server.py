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
import uuid
import json
import logging
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── Configuration ──────────────────────────────────────────────────────────────
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("drdo-rag")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY not found in environment/.env")

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
            model_name="groq/compound",
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


class QuerySource(BaseModel):
    document: str
    page: int
    chunk_preview: str


class QueryResponse(BaseModel):
    answer: str
    sources: List[QuerySource]
    model: str
    chunks_retrieved: int


class DocumentInfo(BaseModel):
    id: str
    filename: str
    pages: int
    chunks: int
    upload_time: str
    size_kb: float


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
        "llm": "llama-3.3-70b-versatile (Groq)",
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
    }
    save_metadata(meta)

    return DocumentInfo(
        id=doc_id,
        filename=file.filename,
        pages=num_pages,
        chunks=num_chunks,
        upload_time=meta[doc_id]["upload_time"],
        size_kb=file_size_kb,
    )


# ── LangGraph Workflow Engine ───────────────────────────────────────────────────
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
    from langchain_core.prompts import PromptTemplate
    question = state["question"]
    docs = state.get("documents", [])
    history = state.get("history")

    chat_history_str = "None"
    if history:
        formatted_turns = []
        for msg in history[-6:]:
            sender_label = "User" if msg.get("sender") == "user" else "Assistant"
            formatted_turns.append(f"{sender_label}: {msg.get('text', '')}")
        chat_history_str = "\n".join(formatted_turns)

    context_str = "\n\n".join([d.page_content for d in docs])
    prompt = PromptTemplate(
        input_variables=["context", "question", "chat_history"],
        template=DRDO_RAG_PROMPT_TEMPLATE,
    ).format(context=context_str, question=question, chat_history=chat_history_str)

    llm = get_llm()
    res = llm.invoke(prompt)
    answer_text = res.content if hasattr(res, 'content') else str(res)

    seen_sources = set()
    sources: List[QuerySource] = []
    for doc in docs:
        m = doc.metadata
        key = (m.get("filename", "Unknown"), m.get("page", 0))
        if key not in seen_sources:
            seen_sources.add(key)
            sources.append(
                QuerySource(
                    document=m.get("filename", "Unknown Document"),
                    page=m.get("page", 0) + 1,
                    chunk_preview=doc.page_content[:120].strip() + "…",
                )
            )

    return {
        "answer": answer_text,
        "sources": sources,
        "model_used": "llama-3.3-70b-versatile (Groq / LangGraph)",
    }


def fallback_kb_node(state: RAGGraphState) -> dict:
    kb_data = load_prefilled_kb()
    if not kb_data:
        raise HTTPException(status_code=404, detail="No documents in knowledge base.")

    kb_text = json.dumps(kb_data, indent=2, ensure_ascii=False)
    llm = get_llm()
    prompt_text = f"""You are VenuAI, an intelligent research assistant for DRDO (Defence Research and Development Organisation).
Answer the user's question accurately using the official preloaded DRDO Knowledge Base below.

FORMATTING RULES:
1. Start with a clear level-3 heading (`### ...`).
2. Present key data (labs, locations, projects, parameters) in a clean Markdown table with headers `| Column 1 | Column 2 |`.
3. Use bullet points (`- `) for key highlights and details.
4. Highlight important lab codes, weapon names, and specifications in **bold**.

--- PRELOADED DRDO KNOWLEDGE BASE ---
{kb_text[:8000]}
--- END KNOWLEDGE BASE ---

Question: {state['question']}

Answer (beautifully formatted with headings, tables, bullet points, and bold text):"""

    res = llm.invoke(prompt_text)
    ans_text = res.content if hasattr(res, 'content') else str(res)
    return {
        "answer": ans_text,
        "sources": [
            QuerySource(
                document="DRDO Master Knowledge Base (Preloaded JSON)",
                page=1,
                chunk_preview="Extracted from DRDO laboratories directory, weapons platforms, and technology clusters.",
            )
        ],
        "model_used": "groq/compound (Preloaded KB / LangGraph Fallback)",
    }


def route_query_node(state: RAGGraphState) -> str:
    meta = load_metadata()
    if meta:
        return "retrieve"
    return "fallback_kb"


# Build & Compile LangGraph State Graph
workflow = StateGraph(RAGGraphState)
workflow.add_node("retrieve", retrieve_node)
workflow.add_node("generate", generate_node)
workflow.add_node("fallback_kb", fallback_kb_node)

workflow.add_conditional_edges(
    START,
    route_query_node,
    {
        "retrieve": "retrieve",
        "fallback_kb": "fallback_kb",
    }
)
workflow.add_edge("retrieve", "generate")
workflow.add_edge("generate", END)
workflow.add_edge("fallback_kb", END)

venuai_graph = workflow.compile()


@app.post("/query", response_model=QueryResponse)
def query_knowledge_base(req: QueryRequest):
    """
    Run a RAG query against the DRDO knowledge base using LangGraph state workflow.
    Returns a grounded AI answer with source citations.
    """
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    try:
        final_state = venuai_graph.invoke({
            "question": req.question,
            "top_k": req.top_k,
            "history": req.history,
            "documents": [],
            "answer": "",
            "sources": [],
            "model_used": "",
        })
        return QueryResponse(
            answer=final_state.get("answer", "No response generated."),
            sources=final_state.get("sources", []),
            model=final_state.get("model_used", "LangGraph Workflow"),
            chunks_retrieved=len(final_state.get("documents", [])),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"LangGraph query error: {e}")
        raise HTTPException(status_code=500, detail=f"LangGraph query execution failed: {str(e)}")


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


# ── Entry Point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("rag_server:app", host="0.0.0.0", port=8000, reload=False)
