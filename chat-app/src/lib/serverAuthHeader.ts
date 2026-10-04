import { NextRequest } from "next/server";

const ACCESS_COOKIE = "venuai_access_token";

/**
 * Builds the Authorization header to forward to the FastAPI backend from
 * the httpOnly access-token cookie set by /api/auth/login. Every RAG proxy
 * route needs this now that the backend requires auth on /query, /upload,
 * /documents, and /analytics/dashboard — see Documents/Backend/authentication.md.
 */
export function authHeader(req: NextRequest): Record<string, string> {
  const token = req.cookies.get(ACCESS_COOKIE)?.value;
  return token ? { Authorization: `Bearer ${token}` } : {};
}
