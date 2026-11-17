import { NextRequest } from "next/server";
import { proxyToBackend } from "@/lib/proxy";

/** GET /traces?limit= - recent traces for the current user. */
export async function GET(req: NextRequest) {
  return proxyToBackend(req, "/traces", { unavailable: "Trace service unavailable." });
}
