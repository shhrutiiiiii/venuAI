import { NextRequest } from "next/server";
import { authHeader } from "@/lib/serverAuthHeader";
import { readJsonCapped } from "@/lib/requestLimits";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";

/**
 * SSE streaming proxy — streams LLM tokens from the FastAPI /query/stream endpoint
 * to the browser using the Streams API (ReadableStream passthrough).
 */
export async function POST(req: NextRequest) {
  try {
    const parsed = await readJsonCapped(req);
    if (!parsed.ok) return parsed.res;
    const body = parsed.body;

    const backendResponse = await fetch(`${RAG_SERVER}/query/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeader(req) },
      body: JSON.stringify(body),
    });

    if (!backendResponse.ok) {
      return new Response(
        JSON.stringify({ error: "Stream request failed", status: backendResponse.status }),
        { status: backendResponse.status, headers: { "Content-Type": "application/json" } }
      );
    }

    // Pass the backend SSE stream directly to the client
    return new Response(backendResponse.body, {
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        Connection: "keep-alive",
      },
    });
  } catch (err) {
    console.error("[RAG Stream] Error:", err);
    return new Response(
      `data: {"error": "RAG stream server unavailable"}\n\n`,
      {
        status: 503,
        headers: { "Content-Type": "text/event-stream" },
      }
    );
  }
}
