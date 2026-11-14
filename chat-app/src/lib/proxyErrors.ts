import { NextResponse } from "next/server";

/**
 * Shared normalisation of upstream (FastAPI) errors for the Next API proxies (S4).
 * Rule: a 5xx or an unparseable upstream body never reaches the browser verbatim - the
 * client gets a generic message plus the upstream request id (for support lookups).
 * 4xx details written for end users (string `detail`, or the first validation message)
 * are kept, clipped. Nothing here logs or returns stack traces, URLs or raw upstream text.
 */
export interface NormalisedError {
  status: number;
  detail: string;
  requestId?: string;
  retryAfter?: string;
}

const GENERIC: Record<number, string> = {
  400: "The request was invalid.",
  401: "Not authenticated.",
  403: "You do not have permission to do that.",
  404: "Not found.",
  409: "The request conflicts with the current state.",
  413: "The request is too large.",
  422: "The request was invalid.",
  429: "Too many requests. Please slow down and try again shortly.",
  502: "The service returned an invalid response.",
  503: "The service is temporarily unavailable.",
  504: "The service took too long to respond.",
};
const MAX_DETAIL = 300;

export function genericMessage(status: number): string {
  return GENERIC[status] ?? (status >= 500 ? "Internal server error." : "Request failed.");
}

function pickDetail(body: unknown): { detail?: string; requestId?: string } {
  if (!body || typeof body !== "object") return {};
  const b = body as { detail?: unknown; request_id?: unknown; trace_id?: unknown };
  const requestId = typeof b.request_id === "string" ? b.request_id : typeof b.trace_id === "string" ? b.trace_id : undefined;
  let detail: string | undefined;
  if (typeof b.detail === "string") detail = b.detail;
  else if (Array.isArray(b.detail) && b.detail.length) {
    const first = b.detail[0] as { loc?: unknown[]; msg?: unknown };
    if (typeof first?.msg === "string") {
      const field = Array.isArray(first.loc) ? first.loc.filter((p) => p !== "body").join(".") : "";
      detail = field ? `${field}: ${first.msg}` : first.msg;
    }
  } else if (b.detail && typeof b.detail === "object" && typeof (b.detail as { message?: unknown }).message === "string") {
    detail = (b.detail as { message: string }).message;
  }
  return { detail: detail?.slice(0, MAX_DETAIL), requestId };
}

/** Normalise an upstream error response (body already read as text, may be empty/non-JSON). */
export function normaliseUpstreamError(status: number, bodyText: string, headers?: Headers): NormalisedError {
  let parsed: unknown;
  try {
    parsed = bodyText ? JSON.parse(bodyText) : undefined;
  } catch {
    parsed = undefined;
  }
  const { detail, requestId } = pickDetail(parsed);
  const keepDetail = status < 500 && detail;
  return {
    status,
    detail: keepDetail ? detail! : genericMessage(status),
    requestId: requestId ?? headers?.get("x-request-id") ?? headers?.get("x-trace-id") ?? undefined,
    retryAfter: status === 429 ? (headers?.get("retry-after") ?? undefined) : undefined,
  };
}

/** Build a NextResponse from a normalised error. */
export function errorResponse(err: { status: number; detail?: string; requestId?: string; retryAfter?: string }): NextResponse {
  const res = NextResponse.json(
    { detail: err.detail ?? genericMessage(err.status), ...(err.requestId ? { request_id: err.requestId } : {}) },
    { status: err.status },
  );
  res.headers.set("Cache-Control", "no-store");
  if (err.retryAfter) res.headers.set("Retry-After", err.retryAfter);
  return res;
}

/** Convenience: read a failed upstream Response and turn it into a safe NextResponse. */
export async function upstreamErrorResponse(res: Response): Promise<NextResponse> {
  const text = await res.text().catch(() => "");
  return errorResponse(normaliseUpstreamError(res.status, text, res.headers));
}

/** The backend could not be reached at all (network error / timeout). */
export function unavailableResponse(message = "Backend unavailable."): NextResponse {
  return errorResponse({ status: 503, detail: message });
}
