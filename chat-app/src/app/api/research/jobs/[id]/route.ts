import { NextRequest } from "next/server";
import { proxyToBackend, seg } from "@/lib/proxy";

type Ctx = { params: Promise<{ id: string }> };

/** Proxy to GET (status/progress/report) and DELETE (cancel) /research/jobs/{id}. */
export async function GET(req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return proxyToBackend(req, `/research/jobs/${seg(id)}`, { unavailable: "Research service unavailable." });
}

export async function DELETE(req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return proxyToBackend(req, `/research/jobs/${seg(id)}`, { unavailable: "Research service unavailable." });
}
