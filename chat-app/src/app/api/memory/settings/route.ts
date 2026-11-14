import { NextRequest } from "next/server";
import { proxyToBackend } from "@/lib/proxy";

/** PUT /memory/settings {enabled:boolean} - per-user opt-in. */
export async function PUT(req: NextRequest) {
  return proxyToBackend(req, "/memory/settings", { withBody: true, unavailable: "Memory service unavailable." });
}
