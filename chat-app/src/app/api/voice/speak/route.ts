import { NextRequest } from "next/server";
import { proxyToBackend } from "@/lib/proxy";

/** Proxy to POST /voice/speak -> audio/wav. */
export async function POST(req: NextRequest) {
  return proxyToBackend(req, "/voice/speak", { withBody: true, timeoutMs: 60_000, unavailable: "Voice service unavailable." });
}
