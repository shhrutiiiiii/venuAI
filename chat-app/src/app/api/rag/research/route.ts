import { NextRequest, NextResponse } from "next/server";
import { authHeader } from "@/lib/serverAuthHeader";
import { readJsonCapped } from "@/lib/requestLimits";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";

// Deep research is synchronous on the backend and can take up to ~120s
// (DeepResearchAgent.MAX_RUNTIME_SECONDS). Allow the platform and our own
// fetch to wait longer than that before giving up.
export const maxDuration = 180;
const UPSTREAM_TIMEOUT_MS = 170_000;

/** Proxy to POST /research — multi-step deep research (plan, sub-questions, synthesis, verification). */
export async function POST(req: NextRequest) {
  try {
    const parsed = await readJsonCapped(req);
    if (!parsed.ok) return parsed.res;
    const body = parsed.body;
    const response = await fetch(`${RAG_SERVER}/research`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeader(req) },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
    const data = await response.json().catch(() => ({ error: "Research server returned a non-JSON response." }));
    return NextResponse.json(data, { status: response.status });
  } catch (err) {
    const timedOut = err instanceof Error && (err.name === "TimeoutError" || err.name === "AbortError");
    console.error("[RAG Research] Error:", err);
    return NextResponse.json(
      {
        error: timedOut
          ? "Deep research timed out. Try a narrower question."
          : "RAG server unavailable. Ensure rag_server.py is running on port 8000.",
      },
      { status: timedOut ? 504 : 503 }
    );
  }
}
