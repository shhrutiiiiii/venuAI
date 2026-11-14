import { NextRequest } from "next/server";
import { proxyToBackend, seg } from "@/lib/proxy";

/** GET /traces/{trace_id}. */
export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return proxyToBackend(req, `/traces/${seg(id)}`, { unavailable: "Trace service unavailable." });
}
