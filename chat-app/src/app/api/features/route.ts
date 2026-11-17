import { NextRequest } from "next/server";
import { proxyToBackend } from "@/lib/proxy";

/** GET /features: optional-feature register (state + token-cost notes). 404 on older backends. */
export async function GET(req: NextRequest) {
  return proxyToBackend(req, "/features", { timeoutMs: 8_000, unavailable: "Features service unavailable." });
}
