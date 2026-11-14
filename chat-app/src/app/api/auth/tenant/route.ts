import { NextRequest, NextResponse } from "next/server";
import { authHeader } from "@/lib/serverAuthHeader";
import { forwardHeaders } from "@/lib/forwardHeaders";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";

/** Proxy to GET /auth/tenant — the caller's own organization/workspace. */
export async function GET(req: NextRequest) {
  try {
    const response = await fetch(`${RAG_SERVER}/auth/tenant`, {
      cache: "no-store",
      headers: { ...authHeader(req), ...forwardHeaders(req) },
    });
    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (err) {
    console.error("[Auth Tenant] Error:", err);
    return NextResponse.json({ error: "Tenant lookup unavailable." }, { status: 503 });
  }
}
