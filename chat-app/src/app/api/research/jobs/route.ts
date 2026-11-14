import { NextRequest } from "next/server";
import { proxyToBackend } from "@/lib/proxy";

/** Proxy to POST /research/jobs (start a job, 202) and GET /research/jobs (list, if deployed). */
export async function POST(req: NextRequest) {
  return proxyToBackend(req, "/research/jobs", { withBody: true, unavailable: "Research service unavailable." });
}

export async function GET(req: NextRequest) {
  return proxyToBackend(req, "/research/jobs", { unavailable: "Research service unavailable." });
}
