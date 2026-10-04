import { NextRequest, NextResponse } from "next/server";
import { authHeader } from "@/lib/serverAuthHeader";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";

/**
 * Proxy to GET /security/violations — the real guardrail audit log,
 * scoped to the caller's own tenant. Backend enforces admin-only access;
 * a non-admin gets the 403 forwarded straight through.
 */
export async function GET(req: NextRequest) {
  try {
    const response = await fetch(`${RAG_SERVER}/security/violations`, {
      cache: "no-store",
      headers: authHeader(req),
    });
    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (err) {
    console.error("[Security Violations] Error:", err);
    return NextResponse.json({ error: "Security Center unavailable." }, { status: 503 });
  }
}
