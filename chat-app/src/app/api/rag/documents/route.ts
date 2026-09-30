import { NextRequest, NextResponse } from "next/server";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";

export async function GET() {
  try {
    const response = await fetch(`${RAG_SERVER}/documents`);
    const data = await response.json();
    return NextResponse.json(data);
  } catch (err) {
    console.error("[RAG Documents] GET Error:", err);
    return NextResponse.json(
      { error: "RAG server unavailable. Ensure rag_server.py is running on port 8000." },
      { status: 503 }
    );
  }
}

export async function DELETE(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const docId = searchParams.get("id");
    if (!docId) {
      return NextResponse.json({ error: "Missing document id" }, { status: 400 });
    }
    const response = await fetch(`${RAG_SERVER}/documents/${docId}`, {
      method: "DELETE",
    });
    const data = await response.json();
    if (!response.ok) {
      return NextResponse.json(data, { status: response.status });
    }
    return NextResponse.json(data);
  } catch (err) {
    console.error("[RAG Documents] DELETE Error:", err);
    return NextResponse.json(
      { error: "RAG server unavailable. Ensure rag_server.py is running on port 8000." },
      { status: 503 }
    );
  }
}
