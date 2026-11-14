import { NextRequest } from "next/server";
import { proxyToBackend } from "@/lib/proxy";

/** GET /approvals/pending. */
export async function GET(req: NextRequest) {
  return proxyToBackend(req, "/approvals/pending", { unavailable: "Approvals service unavailable." });
}
