import { NextRequest, NextResponse } from "next/server";
import { authHeader } from "@/lib/serverAuthHeader";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";

/** Proxy to GET /evidence/{id} — full claim/evidence breakdown for one record. */
export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const response = await fetch(`${RAG_SERVER}/evidence/${encodeURIComponent(id)}`, {
      cache: "no-store",
      headers: authHeader(req),
    });
    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (err) {
    console.error("[Evidence Detail] Error:", err);
    return NextResponse.json({ error: "Evidence Explorer unavailable." }, { status: 503 });
  }
}
