import { NextRequest, NextResponse } from "next/server";
import { authHeader } from "@/lib/serverAuthHeader";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";

/** Proxy to GET /tenants/usage — member/document/dataset counts vs. quotas. */
export async function GET(req: NextRequest) {
  try {
    const response = await fetch(`${RAG_SERVER}/tenants/usage`, {
      cache: "no-store",
      headers: authHeader(req),
    });
    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (err) {
    console.error("[Tenant Usage] Error:", err);
    return NextResponse.json({ error: "Tenant service unavailable." }, { status: 503 });
  }
}
