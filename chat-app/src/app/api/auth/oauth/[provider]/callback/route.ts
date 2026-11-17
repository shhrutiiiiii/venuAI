import { NextRequest, NextResponse } from "next/server";
import { setSessionCookies } from "@/lib/authCookies";
import { forwardHeaders } from "@/lib/forwardHeaders";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";
const BINDING_COOKIE = "venuai_oauth_binding";

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

  const bindingCookie = req.cookies.get(BINDING_COOKIE)?.value;
  const fail = (msg: string) => {
    const r = NextResponse.redirect(new URL(`/login?error=${encodeURIComponent(msg)}`, req.nextUrl.origin));
    r.cookies.set(BINDING_COOKIE, "", { path: "/api/auth/oauth", maxAge: 0 });
    return r;
  };

  if (!code || !state) {
    return fail("oauth_missing_params");
  }

  const redirectUri = `${req.nextUrl.origin}/api/auth/oauth/${provider}/callback`;

  try {
    const backendRes = await fetch(`${RAG_SERVER}/auth/oauth/${encodeURIComponent(provider)}/callback`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...forwardHeaders(req) },
      body: JSON.stringify({ code, state, redirect_uri: redirectUri, binding: bindingCookie ?? null }),
    });
    const data = await backendRes.json();

    if (!backendRes.ok) {
      return fail(typeof data.detail === "string" ? data.detail : "oauth_failed");
    }

    const res = NextResponse.redirect(new URL("/chat", req.nextUrl.origin));
    setSessionCookies(res, data.access_token, data.refresh_token);
    res.cookies.set(BINDING_COOKIE, "", { path: "/api/auth/oauth", maxAge: 0 }); // single use
    return res;
  } catch (err) {
    console.error("[OAuth Callback] Error:", err);
    return fail("oauth_service_unavailable");
  }
}
