import { NextRequest, NextResponse } from "next/server";
import { clearSessionCookies, REFRESH_COOKIE } from "@/lib/authCookies";
import { forwardHeaders } from "@/lib/forwardHeaders";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";

export async function POST(req: NextRequest) {
  const refreshToken = req.cookies.get(REFRESH_COOKIE)?.value;
  if (refreshToken) {
    // Backend revokes the whole refresh family and bumps token_version, so the
    // access token dies immediately, not just at expiry.
    await fetch(`${RAG_SERVER}/auth/logout`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...forwardHeaders(req) },
      body: JSON.stringify({ refresh_token: refreshToken }),
    }).catch(() => {});
  }

  const res = NextResponse.json({ success: true });
  clearSessionCookies(res);
  return res;
}
