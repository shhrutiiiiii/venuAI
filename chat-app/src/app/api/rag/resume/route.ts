import { NextRequest } from "next/server";
import { proxyToBackend } from "@/lib/proxy";

export const maxDuration = 120;

/** POST /query/resume {approval_id} - continue a run that was awaiting approval. */
export async function POST(req: NextRequest) {
  return proxyToBackend(req, "/query/resume", { withBody: true, timeoutMs: 110_000, unavailable: "RAG server unavailable." });
}
