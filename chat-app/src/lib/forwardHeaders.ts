import { NextRequest } from "next/server";

/**
 * Headers every proxy route adds to backend calls so per-IP rate limits and
 * lockout audit see the real browser IP (S1 <-> S4 contract).
 *
 * The backend only believes `x-forwarded-for` when `x-venuai-proxy-secret`
 * matches its TRUSTED_PROXY_SECRET, so set the same value in both envs.
 * The client IP is the entry added by the nearest trusted hop: the Nth entry
 * from the right of the incoming X-Forwarded-For (TRUSTED_PROXY_HOPS, default 1),
 * never the left-most (client-controlled) one.
 */
export function clientIp(req: NextRequest): string {
  const xff = (req.headers.get("x-forwarded-for") ?? "").split(",").map((s) => s.trim()).filter(Boolean);
  const hops = Math.max(1, Number.parseInt(process.env.TRUSTED_PROXY_HOPS ?? "1", 10) || 1);
  if (xff.length > 0) return xff[Math.max(0, xff.length - hops)];
  return req.headers.get("x-real-ip")?.trim() ?? "";
}

export function forwardHeaders(req: NextRequest): Record<string, string> {
  const out: Record<string, string> = {};
  const ip = clientIp(req);
  if (ip) {
    out["X-Forwarded-For"] = ip;
    out["X-Real-IP"] = ip;
  }
  const secret = process.env.TRUSTED_PROXY_SECRET;
  if (secret) out["X-VenuAI-Proxy-Secret"] = secret;
  return out;
}
