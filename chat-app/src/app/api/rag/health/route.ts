import { NextResponse } from "next/server";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://localhost:8000";

export async function GET() {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 3000);

    const response = await fetch(`${RAG_SERVER}/health`, {
      signal: controller.signal,
    });
    clearTimeout(timeoutId);

    const data = await response.json();
    return NextResponse.json(data);
  } catch (err) {
    console.error("[RAG Health] Error:", err);
    return NextResponse.json(
      { status: "offline", error: "RAG server unavailable on port 8000" },
      { status: 503 }
    );
  }
}
