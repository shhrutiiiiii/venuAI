import { NextRequest, NextResponse } from "next/server";
import { authHeader } from "@/lib/serverAuthHeader";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";

/** Proxy to GET /models — the real Phase 1 model registry/catalog. */
export async function GET(req: NextRequest) {
  try {
    const response = await fetch(`${RAG_SERVER}/models`, {
      cache: "no-store",
      headers: authHeader(req),
    });
    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (err) {
    console.error("[Models] Error:", err);
    return NextResponse.json({ error: "Model registry unavailable." }, { status: 503 });
  }
}
