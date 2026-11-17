import { NextRequest } from "next/server";
import { proxyToBackend, seg } from "@/lib/proxy";

/** POST /documents/{id}/summaries: explicit, user-triggered RAPTOR build (503 when RAPTOR is off). */
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return proxyToBackend(req, `/documents/${seg(id)}/summaries`, { timeoutMs: 180_000, unavailable: "Summary service unavailable." });
}
