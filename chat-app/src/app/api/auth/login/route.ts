import { NextRequest, NextResponse } from "next/server";
import { setSessionCookies } from "@/lib/authCookies";
import { forwardHeaders } from "@/lib/forwardHeaders";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";

export async function POST(req: NextRequest) {
  try {
    const { email, password } = await req.json();

    const backendRes = await fetch(`${RAG_SERVER}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...forwardHeaders(req) },
      body: JSON.stringify({ email, password }),
    });
    const data = await backendRes.json();

    if (!backendRes.ok) {
      return NextResponse.json(data, { status: backendRes.status });
    }

    const res = NextResponse.json({ user: data.user });
    // Tokens live only in httpOnly cookies — never readable from client-side JS.
    setSessionCookies(res, data.access_token, data.refresh_token);
    return res;
  } catch (err) {
    console.error("[auth/login] Error:", err);
    return NextResponse.json(
      { detail: "Authentication server unavailable. Ensure rag_server.py is running." },
      { status: 503 }
    );
  }
}
