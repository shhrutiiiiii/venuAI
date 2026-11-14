import { NextRequest, NextResponse } from "next/server";

import { forwardHeaders } from "@/lib/forwardHeaders";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const response = await fetch(`${RAG_SERVER}/auth/reset-password`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...forwardHeaders(req) },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (err) {
    console.error("[Reset Password] Error:", err);
    return NextResponse.json(
      { detail: "Authentication server unavailable. Ensure rag_server.py is running." },
      { status: 503 }
    );
  }
}
