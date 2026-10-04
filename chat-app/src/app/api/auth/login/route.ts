import { NextRequest, NextResponse } from "next/server";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";

const ACCESS_COOKIE = "venuai_access_token";
const REFRESH_COOKIE = "venuai_refresh_token";

export async function POST(req: NextRequest) {
  try {
    const { email, password } = await req.json();

    const backendRes = await fetch(`${RAG_SERVER}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const data = await backendRes.json();

    if (!backendRes.ok) {
      return NextResponse.json(data, { status: backendRes.status });
    }

    const res = NextResponse.json({ user: data.user });

    // Tokens are stored in httpOnly cookies — never readable from client-side
    // JS, unlike the previous mock implementation's localStorage token.
    // See Documents/Frontend/state-management.md.
    res.cookies.set(ACCESS_COOKIE, data.access_token, {
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      path: "/",
      maxAge: 60 * 30, // 30 minutes, matches backend ACCESS_TOKEN_TTL_SECONDS default
    });
    res.cookies.set(REFRESH_COOKIE, data.refresh_token, {
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      path: "/",
      maxAge: 60 * 60 * 24 * 14, // 14 days, matches backend REFRESH_TOKEN_TTL_SECONDS default
    });
    return res;
  } catch (err) {
    console.error("[auth/login] Error:", err);
    return NextResponse.json(
      { detail: "Authentication server unavailable. Ensure rag_server.py is running." },
      { status: 503 }
    );
  }
}
