import { NextRequest, NextResponse } from "next/server";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();

    // Forward all fields including new ones: session_id, user_id, agent_mode, multi_agent_mode
    const response = await fetch(`${RAG_SERVER}/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) {
      return NextResponse.json(data, { status: response.status });
    }
    // Pass through new fields: grounding_score, hallucination_score, verification_flags, annotated_answer
    return NextResponse.json(data);
  } catch (err) {
    console.error("[RAG Query] Error:", err);
    return NextResponse.json(
      { error: "RAG server unavailable. Ensure rag_server.py is running on port 8000." },
      { status: 503 }
    );
  }
}
