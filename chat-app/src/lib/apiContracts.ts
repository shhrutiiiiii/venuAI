/**
 * Typed mirror of the Wave B HTTP / SSE contracts (plan section 2.4).
 * Every field the backend may omit is optional; consumers must never invent
 * values for missing fields (null / undefined means "not measured").
 * Only erasable TypeScript syntax is used so node can run it directly in tests.
 */

// ── /query + /query/stream additive fields ───────────────────────────────────

export interface RetrievedChunk {
  id: string;
  doc: string;
  doc_id?: string;
  page?: number | null;
  /** <= 1200 chars */
  text: string;
  score: number | null;
  retriever?: string;
}

export interface UsageInfo {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface Contradiction {
  claim_a: string;
  claim_b: string;
  doc_a?: string;
  doc_b?: string;
}

export type CorrectiveAction = "re_retrieve" | "regenerate" | "abstain";

export interface CorrectiveInfo {
  iterations: number;
  actions: CorrectiveAction[];
}

export interface MemoryUsed {
  id: string;
  text: string;
}

export interface PendingApproval {
  id: string;
  tool: string;
  args_preview?: unknown;
  description_hash?: string;
}

/** Additive fields shared by the /query response and the stream `done` event. */
export interface QueryExtras {
  retrieved_chunks?: RetrievedChunk[];
  usage?: UsageInfo | null;
  ttft_ms?: number | null;
  latency_ms?: number | null;
  cache_hit?: boolean;
  model_path?: string[];
  followups?: string[];
  contradictions?: Contradiction[];
  corrective?: CorrectiveInfo | null;
  trace_id?: string | null;
  memories_used?: MemoryUsed[];
  pending_approval?: PendingApproval | null;
}

// ── SSE step events ──────────────────────────────────────────────────────────

export type StepStatus = "start" | "end" | "error";

/** Known ids; unknown ids must be tolerated (hence `string` in StepEvent). */
export const KNOWN_STEP_IDS = [
  "cache",
  "rewriting",
  "retrieving",
  "reranking",
  "reading",
  "generating",
  "verifying",
  "correcting",
  "cascade",
] as const;

export interface StepEvent {
  id: string;
  status: StepStatus;
  label?: string;
  t_ms?: number;
  detail?: Record<string, unknown>;
}

// ── Deep research v2 ─────────────────────────────────────────────────────────

export type ResearchDepth = "quick" | "standard" | "deep";

export interface ResearchSubQuestion {
  id: string;
  text: string;
  rationale?: string;
}

export interface ResearchBudget {
  max_subquestions: number;
  max_tokens: number;
  max_seconds: number;
}

export interface ResearchPlanRequest {
  question: string;
  doc_ids?: string[];
  depth: ResearchDepth;
}

export interface ResearchPlanResponse {
  plan_id: string;
  sub_questions: ResearchSubQuestion[];
  budget: ResearchBudget;
}

export interface ResearchJobRequest {
  plan_id?: string;
  question: string;
  sub_questions?: { id: string; text: string }[];
  doc_ids?: string[];
  depth: ResearchDepth;
}

export type ResearchJobStatus =
  | "queued"
  | "running"
  | "done"
  | "failed"
  | "cancelled"
  | "budget_exceeded"
  | "interrupted";

export const TERMINAL_JOB_STATUSES: string[] = ["done", "failed", "cancelled", "budget_exceeded"];

export type ResearchPhase = "planning" | "researching" | "synthesizing" | "citing" | "verifying";

export interface ResearchProgress {
  phase: ResearchPhase | string;
  done: number;
  total: number;
}

export interface ResearchSubtask {
  id: string;
  text: string;
  status: string;
  findings_count?: number;
  tokens?: number;
}

/** Same shape as the a3 citation (see answerTypes.Citation). */
export interface ReportCitation {
  index?: number;
  chunk_id?: string;
  doc_id?: string;
  filename: string;
  page?: number | null;
  quote?: string;
  verified?: boolean;
  char_start?: number | null;
  char_end?: number | null;
  reason?: string | null;
}

export interface ResearchReportV2 {
  markdown: string;
  citations: ReportCitation[];
  verified?: boolean | null;
  grounding_score?: number | null;
  answer_basis?: string;
}

export interface ResearchJob {
  job_id: string;
  status: ResearchJobStatus;
  progress?: ResearchProgress;
  subtasks?: ResearchSubtask[];
  report?: ResearchReportV2;
  error?: string;
}

export interface ResearchJobCreated {
  job_id: string;
  status: "queued";
}

/** Events on GET /research/jobs/{id}/events: step events, progress, done. */
export interface JobStreamEvent {
  step?: StepEvent;
  progress?: ResearchProgress;
  done?: boolean;
}

// ── Export ───────────────────────────────────────────────────────────────────

export type ExportFormat = "docx" | "pdf" | "md";

export interface ExportRequest {
  format: ExportFormat;
  title: string;
  markdown: string;
  citations: ReportCitation[];
}

// ── Voice ────────────────────────────────────────────────────────────────────

export interface VoiceStatus {
  stt: boolean;
  tts: boolean;
}

export interface TranscribeResponse {
  text: string;
  language?: string;
  duration_s?: number;
}

export type VoiceName = "troy" | "hannah" | "austin";

// ── Memory ───────────────────────────────────────────────────────────────────

export interface MemoryItem {
  id: string;
  text: string;
  kind: "semantic" | "episodic";
  created_at?: string;
  source_session?: string | null;
}

// ── Traces ───────────────────────────────────────────────────────────────────

export interface TraceStep {
  id: string;
  label?: string;
  t_start_ms?: number;
  t_end_ms?: number;
  status?: string;
  detail?: Record<string, unknown>;
}

export interface TraceLlmCall {
  model: string;
  task?: string;
  prompt_tokens?: number;
  completion_tokens?: number;
  latency_ms?: number;
  cached?: boolean;
}

export interface TraceRetrieved {
  id: string;
  doc?: string;
  score?: number | null;
  retriever?: string;
}

export interface Trace {
  trace_id: string;
  question_hash?: string;
  started_at?: string;
  total_ms?: number;
  steps?: TraceStep[];
  llm_calls?: TraceLlmCall[];
  retrieved?: TraceRetrieved[];
  model_path?: string[];
  cache_hit?: boolean;
  verification?: Record<string, unknown>;
  prompt_version?: string;
}

// ── Approvals ────────────────────────────────────────────────────────────────

export interface ApprovalDecisionRequest {
  approve: boolean;
  remember: boolean;
}

export interface PendingApprovalItem extends PendingApproval {
  description?: string;
}

// ── Capability probe (own route: /api/capabilities) ──────────────────────────

export interface Capabilities {
  research_jobs: boolean;
  voice: boolean;
  memory: boolean;
  traces: boolean;
  approvals: boolean;
  export: boolean;
  resume: boolean;
}

export const NO_CAPABILITIES: Capabilities = {
  research_jobs: false,
  voice: false,
  memory: false,
  traces: false,
  approvals: false,
  export: false,
  resume: false,
};
