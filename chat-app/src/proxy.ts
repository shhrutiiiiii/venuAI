import { NextResponse, type NextRequest } from "next/server";

/**
 * Per-request response headers (Next 16 "proxy", formerly middleware).
 *
 * CSP ships as Content-Security-Policy-Report-Only first: violations are POSTed to
 * /api/csp-report and logged, nothing is blocked. Set CSP_ENFORCE=1 once chat, workspace,
 * analytics and evidence pages are clean in the logs. 'unsafe-inline' stays for scripts
 * because Next hydration needs inline scripts without a per-request nonce (nonces force
 * every page to dynamic rendering). Dev adds 'unsafe-eval' + ws: for React debugging/HMR.
 * This only sets headers: it never touches or buffers the response body, so SSE streams
 * (/api/rag/stream) are unaffected.
 */
export function buildCsp(isDev: boolean): string {
  const directives = [
    "default-src 'self'",
    `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "img-src 'self' data: blob:",
    "font-src 'self' data: https://fonts.gstatic.com",
    `connect-src 'self'${isDev ? " ws: wss:" : ""}`,
    "media-src 'self' blob:",
    "worker-src 'self' blob:",
    "frame-ancestors 'none'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "report-uri /api/csp-report",
  ];
  return directives.join("; ");
}

export function proxy(req: NextRequest) {
  const res = NextResponse.next();
  const isDev = process.env.NODE_ENV !== "production";
  const header = process.env.CSP_ENFORCE === "1" ? "Content-Security-Policy" : "Content-Security-Policy-Report-Only";
  res.headers.set(header, buildCsp(isDev));
  if (req.nextUrl.pathname.startsWith("/api/")) res.headers.set("Cache-Control", "no-store");
  return res;
}

export const config = {
  // Skip static assets: they carry the baseline headers from next.config.ts.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:png|jpg|jpeg|gif|svg|webp|ico|woff2?)$).*)"],
};
