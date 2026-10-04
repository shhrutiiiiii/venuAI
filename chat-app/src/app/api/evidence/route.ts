import { NextRequest, NextResponse } from "next/server";
import { authHeader } from "@/lib/serverAuthHeader";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";

/** Proxy to GET /evidence — past verification records (Evidence Explorer). */
export async function GET(req: NextRequest) {
  try {
    const qs = req.nextUrl.search;
    const response = await fetch(`${RAG_SERVER}/evidence${qs}`, {
      cache: "no-store",
      headers: authHeader(req),
    });
    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (err) {
    console.error("[Evidence] Error:", err);
    return NextResponse.json({ error: "Evidence Explorer unavailable." }, { status: 503 });
  }
}
