import { NextRequest, NextResponse } from "next/server";
import { authHeader } from "@/lib/serverAuthHeader";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";

/** Proxy to PATCH /tenants — rename and/or requota the caller's own tenant (admin-only). */
export async function PATCH(req: NextRequest) {
  try {
    const body = await req.json();
    const response = await fetch(`${RAG_SERVER}/tenants`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeader(req) },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (err) {
    console.error("[Tenants Patch] Error:", err);
    return NextResponse.json({ error: "Tenant service unavailable." }, { status: 503 });
  }
}
