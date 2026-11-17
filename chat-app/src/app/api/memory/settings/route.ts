import { NextRequest } from "next/server";
import { proxyToBackend } from "@/lib/proxy";

/** GET /memory/settings -> {enabled, feature_enabled}. */
export async function GET(req: NextRequest) {
  return proxyToBackend(req, "/memory/settings", { timeoutMs: 8_000, unavailable: "Memory service unavailable." });
}

/** PUT /memory/settings {enabled:boolean} - per-user opt-in. */
export async function PUT(req: NextRequest) {
  return proxyToBackend(req, "/memory/settings", { withBody: true, unavailable: "Memory service unavailable." });
}
