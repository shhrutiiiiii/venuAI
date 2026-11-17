/** Mock fixtures mirroring plan section 2.4. Used by scripts/test-sse.mjs and for offline UI work. Never shown in the product. */
import type { ResearchJob, ResearchPlanResponse, Trace, QueryExtras } from "../apiContracts";

export const SSE_QUERY_STREAM = [
  'data: {"step": {"id": "cache", "status": "start", "label": "Checking cache", "t_ms": 3}}',
  'data: {"step": {"id": "cache", "status": "end", "label": "Cache miss", "t_ms": 9}}',
  'data: {"step": {"id": "mystery_future_step", "status": "start", "label": "Something new"}}',
  'data: {"step": {"id": "retrieving", "status": "start", "label": "Searching passages", "t_ms": 12}}',
  'data: {"step": {"id": "retrieving", "status": "end", "label": "Searching 12 passages", "t_ms": 412, "detail": {"n": 12, "retriever": "hybrid"}}}',
  'data: {"token": "Agni-V "}',
  'data: {"token": "has a range of 5,000 km."}',
  ": keep-alive",
  'data: {"answer": "Agni-V has a range of 5,000 km.", "verified": true, "grounding_score": 0.91, "cache_hit": false, "model_path": ["openai/gpt-oss-20b", "openai/gpt-oss-120b"], "followups": ["What is its payload?"], "trace_id": "tr_1", "latency_ms": 5200, "ttft_ms": 900}',
  "data: [DONE]",
  "",
].join("\n\n");

export const QUERY_EXTRAS: QueryExtras = {
  retrieved_chunks: [{ id: "c1", doc: "agni.pdf", doc_id: "d1", page: 3, text: "Agni-V has a range of 5,000 km.", score: null, retriever: "hybrid" }],
  usage: { prompt_tokens: 100, completion_tokens: 20, total_tokens: 120 },
  ttft_ms: null,
  latency_ms: 5200,
  cache_hit: false,
  model_path: ["openai/gpt-oss-20b", "openai/gpt-oss-120b"],
  followups: ["What is its payload?"],
  contradictions: [{ claim_a: "range 5,000 km", claim_b: "range 8,000 km", doc_a: "a.pdf", doc_b: "b.pdf" }],
  corrective: { iterations: 1, actions: ["re_retrieve"] },
  trace_id: "tr_1",
  memories_used: [],
};

export const RESEARCH_PLAN: ResearchPlanResponse = {
  plan_id: "p1",
  sub_questions: [
    { id: "q1", text: "What is the range?", rationale: "Core spec" },
    { id: "q2", text: "Who developed it?" },
  ],
  budget: { max_subquestions: 3, max_tokens: 20000, max_seconds: 120 },
};

export const RESEARCH_JOB_DONE: ResearchJob = {
  job_id: "j1",
  status: "done",
  progress: { phase: "verifying", done: 2, total: 2 },
  subtasks: [
    { id: "q1", text: "What is the range?", status: "done", findings_count: 3, tokens: 800 },
    { id: "q2", text: "Who developed it?", status: "done", findings_count: 2, tokens: 600 },
  ],
  report: {
    markdown: "# Report\n\nRange 5,000 km [1].",
    citations: [{ index: 1, filename: "agni.pdf", page: 3, quote: "range of 5,000 km", verified: true }],
    verified: true,
    grounding_score: null,
    answer_basis: "documents",
  },
};

export const SSE_JOB_EVENTS = [
  'data: {"step": {"id": "researching", "status": "start", "label": "Sub-question 1"}}',
  'data: {"progress": {"phase": "researching", "done": 1, "total": 2}}',
  'data: {"done": true}',
  "",
].join("\n\n");

export const TRACE: Trace = {
  trace_id: "tr_1",
  total_ms: 5200,
  steps: [{ id: "retrieving", label: "Searching 12 passages", t_start_ms: 12, t_end_ms: 412, status: "ok" }],
  llm_calls: [{ model: "openai/gpt-oss-20b", task: "generate", prompt_tokens: 100, completion_tokens: 20, latency_ms: 900, cached: false }],
  retrieved: [{ id: "c1", doc: "agni.pdf", score: null, retriever: "hybrid" }],
  model_path: ["openai/gpt-oss-20b"],
  cache_hit: false,
};
