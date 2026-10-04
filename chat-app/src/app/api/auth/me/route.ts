import { NextRequest, NextResponse } from "next/server";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";
const ACCESS_COOKIE = "venuai_access_token";
const REFRESH_COOKIE = "venuai_refresh_token";

async function fetchMe(accessToken: string) {
  return fetch(`${RAG_SERVER}/auth/me`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
}

export async function GET(req: NextRequest) {
  const accessToken = req.cookies.get(ACCESS_COOKIE)?.value;
  const refreshToken = req.cookies.get(REFRESH_COOKIE)?.value;

  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
  }

  let meRes = await fetchMe(accessToken).catch(() => null);

  // Access token expired (short-lived by design) — transparently rotate it
  // using the refresh token rather than forcing a re-login every 30 minutes.
  if ((!meRes || meRes.status === 401) && refreshToken) {
    const refreshRes = await fetch(`${RAG_SERVER}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    }).catch(() => null);

    if (refreshRes && refreshRes.ok) {
      const data = await refreshRes.json();
      meRes = await fetchMe(data.access_token);

      const res = NextResponse.json(data.user);
      res.cookies.set(ACCESS_COOKIE, data.access_token, {
        httpOnly: true,
        sameSite: "lax",
        secure: process.env.NODE_ENV === "production",
        path: "/",
        maxAge: 60 * 30,
      });
      res.cookies.set(REFRESH_COOKIE, data.refresh_token, {
        httpOnly: true,
        sameSite: "lax",
        secure: process.env.NODE_ENV === "production",
        path: "/",
        maxAge: 60 * 60 * 24 * 14,
      });
      return res;
    }
  }

  if (!meRes || !meRes.ok) {
    const res = NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
    res.cookies.delete(ACCESS_COOKIE);
    res.cookies.delete(REFRESH_COOKIE);
    return res;
  }

  const user = await meRes.json();
  return NextResponse.json(user);
}
