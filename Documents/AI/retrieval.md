# Retrieval pipeline

## Purpose
How a question becomes the chunks the LLM sees: query rewriting, hybrid search, cross-encoder reranking, a relevance floor with abstention, and ingest-time contextual chunking. One implementation (`VENUAI/retrieval.py`) serves standard RAG (`retrieve_node`), Agentic RAG (`/research`) and the ReAct `drdo_kb_search` tool.

## Workflow
```
question + history
  -> query_rewriter.rewrite_query      (FAST model, only for follow-ups with history; else skipped)
  -> per query: dense (Chroma) + BM25 (cached index) -> Reciprocal Rank Fusion (k=60)
  -> candidate pool = RETRIEVAL_CANDIDATES x top_k (+ multimodal region hits)
  -> reranker.rerank_scores            (cross-encoder, sigmoid score 0..1)
  -> relevance floor                   (RETRIEVAL_MIN_SCORE)  -> top_k
  -> nothing survives? insufficient_context=True -> `abstain` graph node (no LLM call)
```
- **Original question vs retrieval query.** Only retrieval uses the rewritten standalone query (`state["retrieval_query"]`); generation still answers the user's own words.
- **Reranker order is authoritative.** `context_engineer.rank_chunks` keeps the reranker order (`metadata["retrieval_score"]`); the legacy 50% BM25 / 30% recency / 20% page heuristic is only a tie-break, and the full ranking when chunks carry no score.
- **Abstention.** If every candidate is below the floor, state gets `insufficient_context=True`, `retrieval_scores` (per kept chunk), and the graph routes `retrieve -> abstain -> END` with a fixed "I couldn't find this in the uploaded documents" answer and flag `INSUFFICIENT_CONTEXT`. An authorized-empty corpus (`allowed_doc_ids == []`) keeps the old behaviour (no abstain flag).
- **Fallbacks (graceful degradation).** Reranker cannot load -> one warning, hybrid-fusion order, floor becomes `RETRIEVAL_MIN_SIMILARITY` on Chroma cosine similarity (chunks with a BM25 match or unknown similarity are kept). Rewrite LLM fails -> original question.
- **BM25 index cache.** Built once over the whole corpus (own implementation, no `rank_bm25` dependency) and invalidated by `bump_corpus_version()` on upload/delete, plus when Chroma's chunk count changes. Authorization is applied per query by masking on `allowed_doc_ids`, so the cache is shared safely across users/tenants. (Historic note: the old `BM25Retriever` needed the never-installed `rank_bm25`, so BM25 silently fell back to dense-only.)
- **Contextual chunking (ingest).** `contextual_chunking.add_context_prefixes` asks the FAST model for a 1-2 sentence situating prefix per chunk (concurrency-capped, per-doc cap, aborts after repeated failures; failed chunks are simply unprefixed). The prefix is stored in `metadata["context_prefix"]`; embeddings (`retrieval.index_chunks`), BM25 and the reranker see prefix+chunk, citations/display show the original chunk. Old documents work unchanged. The single ingest finalisation point is marked in `upload_document` (sanitisers go right before the contextualisation call).

## Inputs / Outputs
`retrieve_documents(vs, question, top_k, allowed_doc_ids, history=None, rewrite=True) -> RetrievalResult(documents, scores, insufficient_context, signal, queries, retrieval_query)`.

## Dependencies
`sentence-transformers` (CrossEncoder + embeddings), ChromaDB, the AI gateway (`TaskType.FAST_CLASSIFICATION`, unchanged).

## Configuration
| Env var | Default | Meaning |
|---|---|---|
| `RERANKER_ENABLED` | `true` | `false` skips the cross-encoder |
| `RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | 22M params, CPU-fast, Apache-2.0. Stronger: `BAAI/bge-reranker-base`, `BAAI/bge-reranker-v2-m3` (recalibrate the floor) |
| `RETRIEVAL_CANDIDATES` | `4` | candidate pool = this x `top_k` |
| `RETRIEVAL_MIN_SCORE` | `0.02` | reranker (sigmoid) floor |
| `RETRIEVAL_MIN_SIMILARITY` | `0.2` | cosine floor when no reranker |
| `QUERY_REWRITE_ENABLED` / `QUERY_REWRITE_ALTERNATIVES` | `true` / `1` | rewrite + 0-2 RRF-fused alternatives |
| `CONTEXTUAL_CHUNKING` | `true` | prefix per chunk at upload (costs ~1 FAST call/chunk; set `false` to save tokens) |
| `CONTEXTUAL_CHUNKING_CONCURRENCY` / `_MAX_CHUNKS` | `4` / `200` | parallelism / per-document cap |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | see below |

## Calibration and measured results
`python -m VENUAI.evals.retrieval_bench` builds a temporary Chroma collection of 64 synthetic chunks (16 near-twin systems such as Agni-IV/Agni-V) and compares methods on 58 answerable queries (top-5): old heuristic (dense + legacy rank) hit@1 0.362 / MRR 0.591; hybrid without reranker 0.810 / 0.894; reranked 0.948 / 0.974. Top-1 reranker score: answerable min 0.44 (median 0.9996) vs out-of-corpus max 0.0000, so 0.02 abstains on 6/6 off-topic queries and 0/58 answerable ones. This is synthetic, cleanly separated data: re-check the floor on real documents (log `retrieval_scores`) before tightening it.

## Embedding model
`EMBEDDING_MODEL` defaults to all-MiniLM-L6-v2 so the existing `chroma_db` stays valid. The model name is written to the collection metadata (only on an empty collection; legacy collections are assumed to be the default) and a mismatch logs a loud `EMBEDDING MODEL MISMATCH` warning. Upgrade options: `BAAI/bge-small-en-v1.5` (same size class, better retrieval), `BAAI/bge-m3` (multilingual, 568M), `Qwen/Qwen3-Embedding-0.6B`. **To switch:** stop the server, move `VENUAI/chroma_db` aside, set `EMBEDDING_MODEL`, restart and re-upload the documents (the original PDFs are in `VENUAI/uploads`); queries against an index built with another model return meaningless results. Some models (bge) want a query instruction prefix; not yet applied.

## Security Considerations
`allowed_doc_ids` filters Chroma and masks BM25 every query; the reranker only sees already-authorized candidates. Covered by `tests/test_retrieval_authorization.py` and `tests/test_retrieval_quality.py`.

## Future Improvements
Specialist agents (`agents/base_agent.py`) still use plain Chroma retrieval; the legacy `/query/stream` path likewise. Re-index tooling for embedding migrations. Query-instruction prefixes for bge/Qwen embedders.
