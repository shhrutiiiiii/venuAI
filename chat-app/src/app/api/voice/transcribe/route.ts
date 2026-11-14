import { NextRequest } from "next/server";
import { proxyToBackend } from "@/lib/proxy";

/** Proxy to POST /voice/transcribe (multipart `audio`). Audio is passed through, never stored here. */
export async function POST(req: NextRequest) {
  return proxyToBackend(req, "/voice/transcribe", { withBody: true, timeoutMs: 60_000, unavailable: "Voice service unavailable." });
}
