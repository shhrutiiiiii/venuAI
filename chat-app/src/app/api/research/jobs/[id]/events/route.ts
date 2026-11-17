import { NextRequest } from "next/server";
import { proxySse, seg } from "@/lib/proxy";

export const dynamic = "force-dynamic";

/** SSE passthrough for GET /research/jobs/{id}/events (clients fall back to polling if this fails). */
export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return proxySse(req, `/research/jobs/${seg(id)}/events`);
}
