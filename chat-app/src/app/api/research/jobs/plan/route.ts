import { NextRequest } from "next/server";
import { proxyToBackend } from "@/lib/proxy";

/** Proxy to POST /research/jobs/plan (planning only, one cheap LLM call). */
export async function POST(req: NextRequest) {
  return proxyToBackend(req, "/research/jobs/plan", { withBody: true, timeoutMs: 90_000, unavailable: "Research service unavailable." });
}
