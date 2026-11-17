import { NextRequest, NextResponse } from "next/server";
import { authHeader } from "@/lib/serverAuthHeader";
import { upstreamErrorResponse } from "@/lib/proxyErrors";
import { forwardHeaders } from "@/lib/forwardHeaders";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";

interface ProxyOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  /** Forward the request body (raw bytes, so multipart boundaries survive). */
  withBody?: boolean;
  timeoutMs?: number;
  /** Extra query string to append (defaults to the incoming one). */
  search?: string;
  /** Message returned when the backend cannot be reached at all. */
  unavailable?: string;
}

/**
 * Generic passthrough to the FastAPI backend. Forwards only the auth header
 * (from the httpOnly cookie) and the request Content-Type; never arbitrary client
 * headers. Backend status codes are preserved so the UI can tell
 * "not deployed" (404) / "disabled" (503) from real failures. Works for JSON and
 * binary (audio, docx, pdf) responses alike.
 */
export async function proxyToBackend(req: NextRequest, path: string, opts: ProxyOptions = {}): Promise<Response> {
  const method = opts.method ?? req.method;
  const headers: Record<string, string> = { ...authHeader(req), ...forwardHeaders(req) };
  let body: ArrayBuffer | undefined;
  if (opts.withBody) {
    const ct = req.headers.get("content-type");
    if (ct) headers["Content-Type"] = ct;
    body = await req.arrayBuffer();
  }
  const search = opts.search ?? req.nextUrl.search;
  try {
    const res = await fetch(`${RAG_SERVER}${path}${search}`, {
      method,
      headers,
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(opts.timeoutMs ?? 60_000),
    });
    // 5xx (and non-JSON error bodies) are normalised: no raw upstream text reaches the browser.
    if (res.status >= 500) return await upstreamErrorResponse(res);
    const out = new Headers();
    for (const h of ["content-type", "content-disposition"]) {
      const v = res.headers.get(h);
      if (v) out.set(h, v);
    }
    out.set("Cache-Control", "no-store");
    return new Response(res.status === 204 ? null : await res.arrayBuffer(), { status: res.status, headers: out });
  } catch (err) {
    console.error(`[proxy ${method} ${path}]`, err);
    return NextResponse.json({ detail: opts.unavailable ?? "Backend unavailable." }, { status: 503 });
  }
}

/** Pass an upstream SSE stream straight through (aborts upstream when the client disconnects). */
export async function proxySse(req: NextRequest, path: string): Promise<Response> {
  try {
    const res = await fetch(`${RAG_SERVER}${path}`, {
      headers: { Accept: "text/event-stream", ...authHeader(req), ...forwardHeaders(req) },
      cache: "no-store",
      signal: req.signal,
    });
    if (!res.ok || !res.body) {
      return NextResponse.json({ detail: "Event stream unavailable." }, { status: res.status === 200 ? 502 : res.status });
    }
    return new Response(res.body, {
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
      },
    });
  } catch (err) {
    if (!(err instanceof Error && err.name === "AbortError")) console.error(`[proxy sse ${path}]`, err);
    return NextResponse.json({ detail: "Event stream unavailable." }, { status: 503 });
  }
}

/** Backend path segment guard: ids go into URL paths, so encode them. */
export const seg = (s: string) => encodeURIComponent(s);
