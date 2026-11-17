import { NextRequest } from "next/server";
import { proxyToBackend } from "@/lib/proxy";

/** Proxy to GET /voice/status -> {stt, tts}. 404/503 means voice is not deployed / disabled. */
export async function GET(req: NextRequest) {
  return proxyToBackend(req, "/voice/status", { timeoutMs: 8_000, unavailable: "Voice service unavailable." });
}
