import { NextRequest, NextResponse } from "next/server";
import { authHeader } from "@/lib/serverAuthHeader";
import { NO_CAPABILITIES, type Capabilities } from "@/lib/apiContracts";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";

export const dynamic = "force-dynamic";

/**
 * Feature probe: reads the backend's OpenAPI path list to learn which optional
 * Wave B endpoints are deployed. If the schema cannot be read, every optional
 * feature is reported as unavailable (the UI hides it) - nothing is assumed.
 */
export async function GET(req: NextRequest) {
  try {
    const res = await fetch(`${RAG_SERVER}/openapi.json`, { cache: "no-store", headers: authHeader(req), signal: AbortSignal.timeout(8_000) });
    if (!res.ok) return NextResponse.json(NO_CAPABILITIES);
    const spec = (await res.json()) as { paths?: Record<string, unknown> };
    const paths = Object.keys(spec.paths ?? {});
    const has = (p: string) => paths.includes(p);
    const matches = (re: RegExp) => paths.some((p) => re.test(p));
    const caps: Capabilities = {
      research_jobs: has("/research/jobs") && has("/research/jobs/plan"),
      voice: has("/voice/status") && (has("/voice/transcribe") || has("/voice/speak")),
      memory: has("/memory"),
      traces: matches(/^\/traces\/\{[^}]+\}$/),
      approvals: has("/approvals/pending") && matches(/^\/approvals\/\{[^}]+\}\/decision$/),
      export: has("/export/report"),
      resume: has("/query/resume"),
    };
    return NextResponse.json(caps);
  } catch {
    return NextResponse.json(NO_CAPABILITIES);
  }
}
