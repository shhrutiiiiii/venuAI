import { NextRequest, NextResponse } from "next/server";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";
const ACCESS_COOKIE = "venuai_access_token";
const REFRESH_COOKIE = "venuai_refresh_token";

/**
 * Where the OAuth provider redirects back to. Exchanges the real
 * authorization code for a verified identity via the backend, then sets
 * the same httpOnly cookies /api/auth/login does — an OAuth sign-in
 * ends up in exactly the same session state as a password sign-in.
 */
export async function GET(req: NextRequest) {
  const code = req.nextUrl.searchParams.get("code");
  const state = req.nextUrl.searchParams.get("state");
  const provider = req.nextUrl.pathname.split("/").at(-2) ?? "";

  if (!code || !state) {
    return NextResponse.redirect(new URL("/login?error=oauth_missing_params", req.nextUrl.origin));
  }

  const redirectUri = `${req.nextUrl.origin}/api/auth/oauth/${provider}/callback`;

  try {
    const backendRes = await fetch(`${RAG_SERVER}/auth/oauth/${encodeURIComponent(provider)}/callback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, state, redirect_uri: redirectUri }),
    });
    const data = await backendRes.json();

    if (!backendRes.ok) {
      const detail = encodeURIComponent(data.detail || "oauth_failed");
      return NextResponse.redirect(new URL(`/login?error=${detail}`, req.nextUrl.origin));
    }

    const res = NextResponse.redirect(new URL("/chat", req.nextUrl.origin));
    res.cookies.set(ACCESS_COOKIE, data.access_token, {
      httpOnly: true, sameSite: "lax", secure: process.env.NODE_ENV === "production", path: "/", maxAge: 60 * 30,
    });
    res.cookies.set(REFRESH_COOKIE, data.refresh_token, {
      httpOnly: true, sameSite: "lax", secure: process.env.NODE_ENV === "production", path: "/", maxAge: 60 * 60 * 24 * 14,
    });
    return res;
  } catch (err) {
    console.error("[OAuth Callback] Error:", err);
    return NextResponse.redirect(new URL("/login?error=oauth_service_unavailable", req.nextUrl.origin));
  }
}
