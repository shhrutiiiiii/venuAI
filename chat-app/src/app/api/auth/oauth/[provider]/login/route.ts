import { NextRequest, NextResponse } from "next/server";
import { forwardHeaders } from "@/lib/forwardHeaders";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";
const OAUTH_BINDING_COOKIE = "venuai_oauth_binding";

/**
 * Starts the OAuth flow. The backend returns the provider's authorize URL
 * (PKCE S256 + single-use state) plus a `binding` secret; the binding goes into
 * a short-lived httpOnly cookie so only THIS browser can complete the flow
 * (login-CSRF defence). Errors (e.g. provider not configured — no real
 * client_id/secret, see Documents/Backend/oauth.md) redirect back to /login with
 * a message instead of leaving the user on raw JSON.
 */
export async function GET(req: NextRequest, { params }: { params: Promise<{ provider: string }> }) {
  const { provider } = await params;
  const redirectUri = `${req.nextUrl.origin}/api/auth/oauth/${provider}/callback`;
  const back = (msg: string) =>
    NextResponse.redirect(new URL(`/login?error=${encodeURIComponent(msg)}`, req.nextUrl.origin));

  try {
    const response = await fetch(
      `${RAG_SERVER}/auth/oauth/${encodeURIComponent(provider)}/login?redirect_uri=${encodeURIComponent(redirectUri)}`,
      { cache: "no-store", headers: forwardHeaders(req) }
    );
    const data = await response.json();
    if (!response.ok) {
      return back(typeof data.detail === "string" ? data.detail : "Sign-in with this provider is unavailable.");
    }
    const res = NextResponse.redirect(data.authorize_url);
    res.cookies.set(OAUTH_BINDING_COOKIE, data.binding, {
      httpOnly: true,
      sameSite: "lax", // must accompany the top-level redirect back from the provider
      secure: process.env.NODE_ENV === "production",
      path: "/api/auth/oauth",
      maxAge: 60 * 10,
    });
    return res;
  } catch (err) {
    console.error("[OAuth Login] Error:", err);
    return back("OAuth service unavailable.");
  }
}
