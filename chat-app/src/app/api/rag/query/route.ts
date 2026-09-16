import { NextRequest, NextResponse } from "next/server";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://localhost:8000";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const response = await fetch(`${RAG_SERVER}/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) {
      return NextResponse.json(data, { status: response.status });
    }
    return NextResponse.json(data);
  } catch (err) {
    console.error("[RAG Query] Error:", err);
    return NextResponse.json(
      { error: "RAG server unavailable. Ensure rag_server.py is running on port 8000." },
      { status: 503 }
    );
  }
}
