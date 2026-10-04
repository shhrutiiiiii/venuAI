import { NextRequest, NextResponse } from "next/server";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";

/**
 * Starts the OAuth flow: asks the backend for the provider's real
 * authorize URL, then redirects the browser there. 503 from the backend
 * (provider not configured — no real client_id/client_secret, see
 * Documents/Backend/oauth.md) is forwarded as JSON rather than attempting
 * a redirect to a URL that can't actually complete the flow.
 */
export async function GET(req: NextRequest, { params }: { params: Promise<{ provider: string }> }) {
  const { provider } = await params;
  const redirectUri = `${req.nextUrl.origin}/api/auth/oauth/${provider}/callback`;

  try {
    const response = await fetch(
      `${RAG_SERVER}/auth/oauth/${encodeURIComponent(provider)}/login?redirect_uri=${encodeURIComponent(redirectUri)}`,
      { cache: "no-store" }
    );
    const data = await response.json();
    if (!response.ok) {
      return NextResponse.json(data, { status: response.status });
    }
    return NextResponse.redirect(data.authorize_url);
  } catch (err) {
    console.error("[OAuth Login] Error:", err);
    return NextResponse.json({ error: "OAuth service unavailable." }, { status: 503 });
  }
}
