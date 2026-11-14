import { NextRequest, NextResponse } from "next/server";
import { ACCESS_COOKIE, REFRESH_COOKIE, clearSessionCookies, setSessionCookies } from "@/lib/authCookies";
import { forwardHeaders } from "@/lib/forwardHeaders";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";

async function fetchMe(accessToken: string) {
  return fetch(`${RAG_SERVER}/auth/me`, {
    headers: { Authorization: `Bearer ${accessToken}` },
    cache: "no-store",
  });
}

export async function GET(req: NextRequest) {
  const accessToken = req.cookies.get(ACCESS_COOKIE)?.value;
  const refreshToken = req.cookies.get(REFRESH_COOKIE)?.value;

  const meRes = accessToken ? await fetchMe(accessToken).catch(() => null) : null;

  // Access token expired/revoked (short-lived by design) — transparently rotate
  // it with the refresh token rather than forcing a re-login every 30 minutes.
  if ((!meRes || meRes.status === 401) && refreshToken) {
    const refreshRes = await fetch(`${RAG_SERVER}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...forwardHeaders(req) },
      body: JSON.stringify({ refresh_token: refreshToken }),
    }).catch(() => null);

    if (refreshRes && refreshRes.ok) {
      const data = await refreshRes.json();
      const res = NextResponse.json(data.user);
      setSessionCookies(res, data.access_token, data.refresh_token);
      return res;
    }
  }

  if (!meRes || !meRes.ok) {
    const res = NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
    // Only clear on a definite rejection, not when the backend is merely unreachable.
    if (meRes || !accessToken) clearSessionCookies(res);
    return res;
  }

  return NextResponse.json(await meRes.json());
}
