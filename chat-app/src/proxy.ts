import { NextRequest, NextResponse } from "next/server";
import { csrfViolation } from "@/lib/csrf";

/**
 * Next.js 16 Proxy (formerly middleware): single choke point that applies the
 * CSRF check to every state-changing /api request (S1, ADR-S1.6).
 */
export function proxy(req: NextRequest) {
  const reason = csrfViolation(req.method, req.headers, req.nextUrl.host);
  if (reason) {
    return NextResponse.json({ detail: "Request blocked." }, { status: 403 });
  }
  return NextResponse.next();
}

export const config = {
  matcher: "/api/:path*",
};
