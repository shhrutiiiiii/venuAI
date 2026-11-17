import { NextRequest } from "next/server";
import { proxyToBackend } from "@/lib/proxy";

/** GET /memory (list) and DELETE /memory (forget all) for the current user. */
export async function GET(req: NextRequest) {
  return proxyToBackend(req, "/memory", { unavailable: "Memory service unavailable." });
}

export async function DELETE(req: NextRequest) {
  return proxyToBackend(req, "/memory", { unavailable: "Memory service unavailable." });
}
