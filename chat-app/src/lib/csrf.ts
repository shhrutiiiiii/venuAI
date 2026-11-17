/**
 * CSRF defence for cookie-authenticated proxy routes (S1, ADR-S1.6).
 *
 * Two independent checks on every state-changing /api request (enforced centrally
 * in src/proxy.ts, so no route can forget it):
 *   1. same-origin: Sec-Fetch-Site must be "same-origin", or (older browsers) the
 *      Origin header's host must equal the request host;
 *   2. custom header `X-Requested-With: venuai`, which a cross-site form or
 *      <img>/<script> request cannot set and which forces a CORS preflight for
 *      cross-origin fetch().
 * The browser side adds the header to every same-origin unsafe fetch via
 * installCsrfFetch() (called once from AuthContext).
 */

export const CSRF_HEADER = "x-requested-with";
export const CSRF_VALUE = "venuai";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export function isUnsafeMethod(method: string): boolean {
  return !SAFE_METHODS.has(method.toUpperCase());
}

/** Returns a reason string when the request must be rejected, else null. Pure (testable). */
export function csrfViolation(method: string, headers: Headers, host: string): string | null {
  if (!isUnsafeMethod(method)) return null;

  const fetchSite = headers.get("sec-fetch-site");
  const origin = headers.get("origin");
  if (fetchSite) {
    if (fetchSite !== "same-origin") return "cross-site request";
  } else if (origin) {
    let originHost = "";
    try {
      originHost = new URL(origin).host;
    } catch {
      return "malformed origin";
    }
    const allowed = new Set([host, headers.get("x-forwarded-host") ?? ""]);
    if (!allowed.has(originHost)) return "cross-origin request";
  } else {
    return "missing origin";
  }

  if (headers.get(CSRF_HEADER) !== CSRF_VALUE) return "missing CSRF header";
  return null;
}

/** Browser only: add the CSRF header to same-origin unsafe fetches. Idempotent. */
export function installCsrfFetch(): void {
  if (typeof window === "undefined") return;
  const w = window as typeof window & { __venuaiCsrf?: boolean };
  if (w.__venuaiCsrf) return;
  w.__venuaiCsrf = true;
  const original = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const isReq = typeof Request !== "undefined" && input instanceof Request;
    const method = (init?.method ?? (isReq ? (input as Request).method : "GET")).toUpperCase();
    if (isUnsafeMethod(method)) {
      const url = new URL(isReq ? (input as Request).url : String(input), window.location.href);
      if (url.origin === window.location.origin) {
        const headers = new Headers(init?.headers ?? (isReq ? (input as Request).headers : undefined));
        if (!headers.has(CSRF_HEADER)) headers.set(CSRF_HEADER, CSRF_VALUE);
        return original(input, { ...init, headers });
      }
    }
    return original(input, init);
  };
}
