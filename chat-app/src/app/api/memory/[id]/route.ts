import { NextRequest } from "next/server";
import { proxyToBackend, seg } from "@/lib/proxy";

/** DELETE /memory/{id}. */
export async function DELETE(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return proxyToBackend(req, `/memory/${seg(id)}`, { unavailable: "Memory service unavailable." });
}
