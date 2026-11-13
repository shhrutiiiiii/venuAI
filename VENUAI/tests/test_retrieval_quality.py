"""
Retrieval-quality upgrade: BM25 cache + authorization, reranker ordering /
floor / abstention / fallback, query rewriting (LLM mocked), contextual
chunking (LLM mocked), embedding-model mismatch warning, abstain graph node.
No real models or network are used.
"""
import logging

import pytest
from langchain_core.documents import Document

from VENUAI import contextual_chunking, query_rewriter, reranker, retrieval, rag_server


class FakeVS:
    """Same .get()/.as_retriever() shape as the Chroma wrapper; counts get() calls."""

    def __init__(self, docs):
        self.docs = docs
        self.get_calls = 0

    def get(self):
        self.get_calls += 1
        return {"documents": [d.page_content for d in self.docs], "metadatas": [d.metadata for d in self.docs]}

    def as_retriever(self, search_type, search_kwargs):
        outer = self

        class R:
            def invoke(self, q):
                allowed = search_kwargs.get("filter", {}).get("doc_id", {}).get("$in")
                docs = [d for d in outer.docs if allowed is None or d.metadata["doc_id"] in allowed]
                return [Document(page_content=d.page_content, metadata=dict(d.metadata)) for d in docs][: search_kwargs["k"]]
        return R()


def _docs():
    return [
        Document(page_content="Agni-IV has a range of 4000 km", metadata={"doc_id": "a", "page": 0}),
        Document(page_content="Agni-V has a range of 5500 km", metadata={"doc_id": "b", "page": 0}),
        Document(page_content="Tejas is a light combat aircraft", metadata={"doc_id": "c", "page": 0}),
    ]


@pytest.fixture(autouse=True)
def _fresh():
    retrieval.bump_corpus_version()
    reranker.reset_reranker()
    yield
    reranker.reset_reranker()


def test_bm25_index_cached_and_invalidated_on_version_bump():
    vs = FakeVS(_docs())
    retrieval.retrieve_documents(vs, "Agni-V range", 2, None, rewrite=False, include_regions=False)
    retrieval.retrieve_documents(vs, "Agni-IV range", 2, None, rewrite=False, include_regions=False)
    assert vs.get_calls == 1  # corpus read once, not per query
    retrieval.bump_corpus_version()
    retrieval.retrieve_documents(vs, "Agni-V range", 2, None, rewrite=False, include_regions=False)
    assert vs.get_calls == 2


def test_bm25_respects_allowed_doc_ids_even_when_cached():
    vs = FakeVS(_docs())
    retrieval.retrieve_documents(vs, "Agni-V", 3, None, rewrite=False, include_regions=False)  # warms cache with all docs
    res = retrieval.retrieve_documents(vs, "Agni-V range", 3, ["a"], rewrite=False, include_regions=False)
    assert {d.metadata["doc_id"] for d in res.documents} == {"a"}
    empty = retrieval.retrieve_documents(vs, "Agni-V range", 3, [], rewrite=False, include_regions=False)
    assert empty.documents == [] and empty.insufficient_context is False


def test_bm25_exact_identifier_beats_twin():
    index = retrieval.BM25Index([d.page_content for d in _docs()], [d.metadata for d in _docs()])
    top = index.search("Agni-V range", 3, None)
    assert index.texts[top[0][0]].startswith("Agni-V")


def test_reranker_orders_and_floor_drops_low_scores(monkeypatch):
    monkeypatch.setattr(reranker, "rerank_scores",
                        lambda q, texts: [0.9 if "Agni-V" in t else 0.001 for t in texts])
    vs = FakeVS(_docs())
    res = retrieval.retrieve_documents(vs, "range of Agni-V", 3, None, rewrite=False, include_regions=False)
    assert res.signal == "rerank"
    assert [d.page_content for d in res.documents] == ["Agni-V has a range of 5500 km"]
    assert res.scores[0]["rerank_score"] == 0.9
    assert res.documents[0].metadata["retrieval_score"] == 0.9
    assert not res.insufficient_context


def test_abstains_when_nothing_clears_floor(monkeypatch):
    monkeypatch.setattr(reranker, "rerank_scores", lambda q, texts: [0.0001] * len(texts))
    res = retrieval.retrieve_documents(FakeVS(_docs()), "biryani recipe", 3, None, rewrite=False, include_regions=False)
    assert res.documents == [] and res.insufficient_context is True


def test_floor_is_configurable(monkeypatch):
    monkeypatch.setattr(reranker, "rerank_scores", lambda q, texts: [0.3] * len(texts))
    monkeypatch.setenv("RETRIEVAL_MIN_SCORE", "0.5")
    res = retrieval.retrieve_documents(FakeVS(_docs()), "x", 3, None, rewrite=False, include_regions=False)
    assert res.insufficient_context


def test_fallback_without_reranker_keeps_working(monkeypatch):
    monkeypatch.setattr(reranker, "rerank_scores", lambda q, texts: None)
    res = retrieval.retrieve_documents(FakeVS(_docs()), "Agni-V range", 2, None, rewrite=False, include_regions=False)
    assert res.signal in ("fusion", "similarity")
    assert any(d.page_content.startswith("Agni-V") for d in res.documents)


def test_reranker_load_failure_returns_none_and_warns(monkeypatch, caplog):
    monkeypatch.setenv("RERANKER_ENABLED", "true")
    monkeypatch.setenv("RERANKER_MODEL", "no/such-model-xyz")
    import sentence_transformers

    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(sentence_transformers, "CrossEncoder", boom)
    with caplog.at_level(logging.WARNING, logger="drdo-rag"):
        assert reranker.rerank_scores("q", ["t"]) is None
        assert reranker.rerank_scores("q", ["t"]) is None  # no retry storm
    assert sum("could not load" in r.message for r in caplog.records) == 1


def test_rank_chunks_preserves_reranker_order():
    from VENUAI.context_engineer import rank_chunks
    a = Document(page_content="low", metadata={"retrieval_score": 0.1, "page": 0})
    b = Document(page_content="high", metadata={"retrieval_score": 0.9, "page": 40})
    assert [d.page_content for d in rank_chunks([a, b], "q")] == ["high", "low"]


# ── query rewriting ──────────────────────────────────────────────────────────

HIST = [{"sender": "user", "text": "Tell me about Agni-V"}, {"sender": "ai", "text": "Agni-V is a missile."}]


def test_rewrite_skipped_without_history_or_when_standalone():
    def never(prompt, n):
        raise AssertionError("LLM must not be called")
    assert query_rewriter.rewrite_query("what about its range?", [], never).retrieval_query == "what about its range?"
    q = "What is the maximum operational range of the Agni-V missile in kilometres?"
    assert query_rewriter.rewrite_query(q, HIST, never).used_llm is False


def test_rewrite_followup_with_alternatives(monkeypatch):
    monkeypatch.setenv("QUERY_REWRITE_ENABLED", "true")
    calls = []

    def fake(prompt, n):
        calls.append(prompt)
        return '{"standalone": "range of Agni-V missile", "alternatives": ["Agni-V maximum distance km"]}'
    r = query_rewriter.rewrite_query("what about its range?", HIST, fake)
    assert r.used_llm and r.retrieval_query == "range of Agni-V missile"
    assert r.alternatives == ["Agni-V maximum distance km"]
    assert "Tell me about Agni-V" in calls[0]


def test_rewrite_failure_falls_back_to_original(monkeypatch):
    monkeypatch.setenv("QUERY_REWRITE_ENABLED", "true")

    def boom(prompt, n):
        raise RuntimeError("rate limited")
    r = query_rewriter.rewrite_query("and its range?", HIST, boom)
    assert r.retrieval_query == "and its range?" and not r.used_llm
    assert query_rewriter.rewrite_query("and its range?", HIST, lambda p, n: "not json").retrieval_query == "and its range?"


def test_retrieve_documents_uses_rewritten_query_and_fuses_alternatives(monkeypatch):
    monkeypatch.setattr(query_rewriter, "rewrite_query",
                        lambda q, h, complete=None: query_rewriter.RewriteResult("Agni-V range", ["Agni-IV range"], True))
    seen = []
    monkeypatch.setattr(reranker, "rerank_scores", lambda q, texts: seen.append(q) or [0.9] * len(texts))
    res = retrieval.retrieve_documents(FakeVS(_docs()), "and its range?", 3, None, history=HIST, include_regions=False)
    assert res.retrieval_query == "Agni-V range" and res.queries == ["Agni-V range", "Agni-IV range"]
    assert seen == ["Agni-V range"]
    assert {"Agni-V", "Agni-IV"} <= {d.page_content.split()[0] for d in res.documents}


def test_agentic_expand_query_uses_llm(monkeypatch):
    from VENUAI import agentic_rag
    monkeypatch.setenv("QUERY_REWRITE_ENABLED", "true")
    monkeypatch.setattr(query_rewriter, "fast_complete", lambda p, n=200: "Agni-V maximum strike distance")
    assert agentic_rag._expand_query("Agni-V range", 1) == "Agni-V maximum strike distance"
    monkeypatch.setattr(query_rewriter, "fast_complete", lambda p, n=200: (_ for _ in ()).throw(RuntimeError("x")))
    assert agentic_rag._expand_query("Agni-V range", 1) == "Agni-V range"


# ── contextual chunking ──────────────────────────────────────────────────────

def _chunks(n=3):
    return [Document(page_content=f"chunk {i}", metadata={"page": 0}) for i in range(n)]


def test_contextual_chunking_adds_prefix_and_keeps_text(monkeypatch):
    monkeypatch.setenv("CONTEXTUAL_CHUNKING", "true")
    chunks = _chunks()
    n = contextual_chunking.add_context_prefixes(
        chunks, [Document(page_content="page text", metadata={"page": 0})], "r.pdf", complete=lambda p, t: "From the Agni-V report.")
    assert n == 3
    assert all(c.metadata["context_prefix"] == "From the Agni-V report." for c in chunks)
    assert chunks[0].page_content == "chunk 0"


def test_contextual_chunking_disabled_and_failure(monkeypatch):
    chunks = _chunks()
    assert contextual_chunking.add_context_prefixes(chunks, [], "r.pdf", complete=lambda p, t: "x") == 0  # env false in tests
    monkeypatch.setenv("CONTEXTUAL_CHUNKING", "true")

    def boom(p, t):
        raise RuntimeError("429")
    assert contextual_chunking.add_context_prefixes(chunks, [], "r.pdf", complete=boom) == 0
    assert all("context_prefix" not in c.metadata for c in chunks)


def test_prefix_is_indexed_by_bm25_but_not_displayed():
    d = Document(page_content="The range is 5500 km.", metadata={"doc_id": "x", "context_prefix": "Agni-V test report"})
    idx = retrieval.BM25Index([d.page_content], [d.metadata])
    assert idx.search("Agni-V", 1, None)
    assert idx.document(0).page_content == "The range is 5500 km."


def test_index_chunks_embeds_prefix_but_stores_original_and_bumps_cache():
    stored = {}

    class Emb:
        def embed_documents(self, texts):
            stored["embedded"] = texts
            return [[0.0]] * len(texts)

    class Col:
        def upsert(self, **kw):
            stored.update(kw)

    class VS:
        _embedding_function = Emb()
        _collection = Col()

    v0 = retrieval._corpus_version
    retrieval.index_chunks(VS(), [Document(page_content="orig", metadata={"context_prefix": "ctx", "doc_id": "d"})])
    assert stored["embedded"] == ["ctx\norig"] and stored["documents"] == ["orig"]
    assert retrieval._corpus_version == v0 + 1


# ── embedding mismatch + abstain node ────────────────────────────────────────

def test_embedding_model_mismatch_warns(monkeypatch, caplog):
    class Col:
        metadata = {"embedding_model": "old/model"}

        def count(self):
            return 5

    class VS:
        _collection = Col()
    monkeypatch.setenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    with caplog.at_level(logging.WARNING, logger="drdo-rag"):
        rag_server._check_embedding_model(VS())
    assert any("MISMATCH" in r.message for r in caplog.records)


def test_legacy_collection_with_default_model_is_silent(monkeypatch, caplog):
    class Col:
        metadata = None

        def count(self):
            return 5

        def modify(self, **kw):
            raise AssertionError("must not modify a non-empty legacy collection")

    class VS:
        _collection = Col()
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    with caplog.at_level(logging.WARNING, logger="drdo-rag"):
        rag_server._check_embedding_model(VS())
    assert not caplog.records


def test_graph_abstains_without_calling_llm(monkeypatch):
    monkeypatch.setattr(rag_server, "load_metadata", lambda: {"d": {}})
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: FakeVS(_docs()))
    monkeypatch.setattr(reranker, "rerank_scores", lambda q, texts: [0.0] * len(texts))
    monkeypatch.setattr(rag_server, "get_llm", lambda: (_ for _ in ()).throw(AssertionError("LLM called")))
    out = rag_server.venuai_graph.invoke({
        "question": "biryani recipe", "top_k": 3, "history": [], "documents": [], "allowed_doc_ids": None,
        "working_memory": [], "semantic_memories": [],
    })
    assert out["insufficient_context"] is True
    assert "couldn't find" in out["answer"]
    assert out["documents"] == [] and out["verification_flags"] == ["INSUFFICIENT_CONTEXT"]
