import { NextRequest, NextResponse } from "next/server";
import { authHeader } from "@/lib/serverAuthHeader";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";

/** Proxy to GET/POST /tenants/members — list/add members of the caller's own tenant (admin-only). */
export async function GET(req: NextRequest) {
  try {
    const response = await fetch(`${RAG_SERVER}/tenants/members`, {
      cache: "no-store",
      headers: authHeader(req),
    });
    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (err) {
    console.error("[Tenant Members] GET Error:", err);
    return NextResponse.json({ error: "Tenant service unavailable." }, { status: 503 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const response = await fetch(`${RAG_SERVER}/tenants/members`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeader(req) },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (err) {
    console.error("[Tenant Members] POST Error:", err);
    return NextResponse.json({ error: "Tenant service unavailable." }, { status: 503 });
  }
}
