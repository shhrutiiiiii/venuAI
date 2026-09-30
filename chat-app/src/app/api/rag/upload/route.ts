import { NextRequest, NextResponse } from "next/server";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";

export async function POST(req: NextRequest) {
  try {
    const formData = await req.formData();
    const response = await fetch(`${RAG_SERVER}/upload`, {
      method: "POST",
      body: formData,
    });
    const data = await response.json();
    if (!response.ok) {
      return NextResponse.json(data, { status: response.status });
    }
    return NextResponse.json(data);
  } catch (err) {
    console.error("[RAG Upload] Error:", err);
    return NextResponse.json(
      { error: "RAG server unavailable. Ensure rag_server.py is running on port 8000." },
      { status: 503 }
    );
  }
}
