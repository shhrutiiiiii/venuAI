import { NextRequest, NextResponse } from "next/server";
import { authHeader } from "@/lib/serverAuthHeader";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";

/** Proxy to GET /graph/search?q=&entity_type= — knowledge graph entity search. */
export async function GET(req: NextRequest) {
  try {
    const qs = req.nextUrl.search;
    const response = await fetch(`${RAG_SERVER}/graph/search${qs}`, {
      cache: "no-store",
      headers: authHeader(req),
    });
    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (err) {
    console.error("[Graph Search] Error:", err);
    return NextResponse.json({ error: "Knowledge graph unavailable." }, { status: 503 });
  }
}
