import { NextRequest } from "next/server";
import { proxyToBackend, seg } from "@/lib/proxy";

/** Proxy to POST /research/jobs/{id}/resume (interrupted jobs). */
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return proxyToBackend(req, `/research/jobs/${seg(id)}/resume`, { unavailable: "Research service unavailable." });
}
