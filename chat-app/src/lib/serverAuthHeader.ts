import { NextRequest } from "next/server";
import { ACCESS_COOKIE } from "@/lib/authCookies";
import { forwardHeaders } from "@/lib/forwardHeaders";

/**
 * Builds the Authorization header to forward to the FastAPI backend from
 * the httpOnly access-token cookie set by /api/auth/login. Every RAG proxy
 * route needs this now that the backend requires auth on /query, /upload,
 * /documents, and /analytics/dashboard — see Documents/Backend/authentication.md.
 *
 * Also carries the client-IP / proxy-secret headers (S1 forwardHeaders) so every direct
 * backend call (query, stream, research, upload, ...) is rate-limited and audited per real IP.
 */
export function authHeader(req: NextRequest): Record<string, string> {
  const token = req.cookies.get(ACCESS_COOKIE)?.value;
  const fwd = forwardHeaders(req);
  return token ? { Authorization: `Bearer ${token}`, ...fwd } : fwd;
}
