import type {
  ResearchDepth,
  ResearchJob,
  ResearchJobCreated,
  ResearchPlanResponse,
  ResearchSubQuestion,
} from "@/lib/apiContracts";

/** Result wrapper: `unavailable` means the endpoint is not deployed / disabled (caller should fall back or hide). */
export type ApiResult<T> = { ok: true; data: T } | { ok: false; unavailable: boolean; status: number; message: string };

async function call<T>(url: string, init?: RequestInit): Promise<ApiResult<T>> {
  try {
    const r = await fetch(url, { cache: "no-store", ...init });
    const body = (await r.json().catch(() => ({}))) as Record<string, unknown>;
    if (!r.ok) {
      const detail = typeof body.detail === "string" ? body.detail : typeof body.error === "string" ? body.error : `Request failed (${r.status})`;
      return { ok: false, unavailable: [404, 405, 501, 503].includes(r.status), status: r.status, message: detail };
    }
    return { ok: true, data: body as T };
  } catch {
    return { ok: false, unavailable: true, status: 0, message: "Cannot reach the research service." };
  }
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const planResearch = (question: string, depth: ResearchDepth, doc_ids?: string[]) =>
  call<ResearchPlanResponse>("/api/research/jobs/plan", json("POST", { question, depth, ...(doc_ids?.length ? { doc_ids } : {}) }));

export const startResearchJob = (req: { question: string; depth: ResearchDepth; plan_id?: string; sub_questions: ResearchSubQuestion[]; doc_ids?: string[] }) =>
  call<ResearchJobCreated>(
    "/api/research/jobs",
    json("POST", {
      question: req.question,
      depth: req.depth,
      plan_id: req.plan_id,
      sub_questions: req.sub_questions.map((q) => ({ id: q.id, text: q.text })),
      ...(req.doc_ids?.length ? { doc_ids: req.doc_ids } : {}),
    })
  );

export const getResearchJob = (id: string) => call<ResearchJob>(`/api/research/jobs/${encodeURIComponent(id)}`);

export const cancelResearchJob = (id: string) => call<unknown>(`/api/research/jobs/${encodeURIComponent(id)}`, { method: "DELETE" });

export const resumeResearchJob = (id: string) => call<unknown>(`/api/research/jobs/${encodeURIComponent(id)}/resume`, { method: "POST" });
