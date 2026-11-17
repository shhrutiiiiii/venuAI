import { NextRequest } from "next/server";
import { proxyToBackend, seg } from "@/lib/proxy";

/** POST /approvals/{id}/decision {approve, remember}. */
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return proxyToBackend(req, `/approvals/${seg(id)}/decision`, { withBody: true, unavailable: "Approvals service unavailable." });
}
