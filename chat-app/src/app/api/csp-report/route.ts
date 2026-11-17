import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const MAX_BYTES = 8 * 1024;
const clip = (v: unknown) => (typeof v === "string" ? v.split("?")[0].slice(0, 200) : undefined);

/**
 * CSP violation report sink (Report-Only phase). Public by design (browsers send reports
 * without credentials), so it is strictly bounded: tiny body, logs only a few clipped
 * fields with query strings stripped, never echoes anything back.
 */
export async function POST(req: NextRequest) {
  try {
    const len = Number(req.headers.get("content-length") ?? "0");
    if (len > MAX_BYTES) return new NextResponse(null, { status: 413 });
    const text = (await req.text()).slice(0, MAX_BYTES);
    const parsed = JSON.parse(text);
    const first = Array.isArray(parsed) ? parsed[0]?.body : (parsed["csp-report"] ?? parsed);
    console.warn("[csp-report]", {
      directive: clip(first?.["violated-directive"] ?? first?.effectiveDirective),
      blocked: clip(first?.["blocked-uri"] ?? first?.blockedURL),
      document: clip(first?.["document-uri"] ?? first?.documentURL),
    });
  } catch {
    /* malformed report: ignore */
  }
  return new NextResponse(null, { status: 204 });
}
