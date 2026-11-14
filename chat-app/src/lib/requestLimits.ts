import { NextRequest, NextResponse } from "next/server";

export const MAX_PDF_UPLOAD_BYTES = 26 * 1024 * 1024; // backend cap 25 MB + multipart framing
export const MAX_DATASET_UPLOAD_BYTES = 21 * 1024 * 1024; // backend cap 20 MB
export const MAX_JSON_BODY_BYTES = 256 * 1024; // question 4k + 20 history items x 4k, with headroom

/**
 * Reject on the declared Content-Length BEFORE the body is buffered (req.formData()/req.json()).
 * Returns a 413/411 response when over the limit, else null. The backend still enforces its own
 * streaming cap, so a missing/lying Content-Length is not a bypass.
 */
export function rejectOversize(req: NextRequest, maxBytes: number): NextResponse | null {
  const raw = req.headers.get("content-length");
  if (raw === null) return null;
  const n = Number(raw);
  if (!Number.isFinite(n) || n < 0) {
    return NextResponse.json({ error: "Invalid Content-Length." }, { status: 400 });
  }
  if (n > maxBytes) {
    return NextResponse.json({ error: "Request body too large." }, { status: 413 });
  }
  return null;
}

/** Read a JSON body with a hard byte cap enforced while streaming (not just via the header). */
export async function readJsonCapped(
  req: NextRequest,
  maxBytes: number = MAX_JSON_BODY_BYTES,
): Promise<{ ok: true; body: unknown } | { ok: false; res: NextResponse }> {
  const early = rejectOversize(req, maxBytes);
  if (early) return { ok: false, res: early };
  const reader = req.body?.getReader();
  if (!reader) return { ok: false, res: NextResponse.json({ error: "Empty body." }, { status: 400 }) };
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > maxBytes) {
      await reader.cancel();
      return { ok: false, res: NextResponse.json({ error: "Request body too large." }, { status: 413 }) };
    }
    chunks.push(value);
  }
  try {
    const text = new TextDecoder().decode(Buffer.concat(chunks));
    return { ok: true, body: JSON.parse(text) };
  } catch {
    return { ok: false, res: NextResponse.json({ error: "Invalid JSON." }, { status: 400 }) };
  }
}
