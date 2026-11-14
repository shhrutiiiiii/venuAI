/**
 * Tolerant SSE parsing for /query/stream and /research/jobs/{id}/events.
 * - Buffers across network chunk boundaries (a `data:` line can be split).
 * - Handles \n, \r\n, multi-line data fields, comments and `[DONE]`.
 * - Never throws: malformed JSON becomes `{ kind: "text" }`; unknown shapes
 *   are returned as `{ kind: "unknown" }` so callers can ignore them.
 * Erasable TypeScript only (runnable by node in scripts/test-sse.mjs).
 */

export type ParsedEvent =
  | { kind: "token"; token: string }
  | { kind: "step"; step: { id: string; status: "start" | "end" | "error"; label?: string; t_ms?: number; detail?: Record<string, unknown> } }
  | { kind: "progress"; progress: { phase: string; done: number; total: number } }
  | { kind: "done"; data: Record<string, unknown> }
  | { kind: "error"; message: string }
  | { kind: "text"; text: string }
  | { kind: "unknown"; data: Record<string, unknown> };

/** Incremental line-oriented SSE parser; feed decoded text, get complete `data` payloads. */
export class SseBuffer {
  private buf = "";
  private dataLines: string[] = [];

  push(chunk: string): string[] {
    this.buf += chunk;
    const out: string[] = [];
    while (true) {
      const idx = this.buf.search(/\r\n|\n|\r/);
      if (idx === -1) break;
      const isCr = this.buf[idx] === "\r";
      // A lone trailing "\r" might be the first half of "\r\n": wait for more.
      if (isCr && idx + 1 === this.buf.length) break;
      const len = isCr && this.buf[idx + 1] === "\n" ? 2 : 1;
      const line = this.buf.slice(0, idx);
      this.buf = this.buf.slice(idx + len);
      this.consumeLine(line, out);
    }
    return out;
  }

  /** Flush at end of stream (a final event without trailing blank line). */
  end(): string[] {
    const out: string[] = [];
    if (this.buf) {
      this.consumeLine(this.buf.replace(/\r$/, ""), out);
      this.buf = "";
    }
    if (this.dataLines.length) {
      out.push(this.dataLines.join("\n"));
      this.dataLines = [];
    }
    return out;
  }

  private consumeLine(line: string, out: string[]) {
    if (line === "") {
      if (this.dataLines.length) {
        out.push(this.dataLines.join("\n"));
        this.dataLines = [];
      }
      return;
    }
    if (line.startsWith(":")) return; // comment / keep-alive
    if (line.startsWith("data:")) {
      this.dataLines.push(line.slice(5).replace(/^ /, ""));
      // Older backends emit one JSON per `data:` line, sometimes without blank
      // separators; flush eagerly once the accumulated payload is complete.
      const joined = this.dataLines.join("\n");
      if (looksComplete(joined)) {
        out.push(joined);
        this.dataLines = [];
      }
    }
    // event:/id:/retry: fields are ignored.
  }
}

function looksComplete(s: string): boolean {
  const t = s.trim();
  if (t === "[DONE]") return true;
  if (t.startsWith("{")) {
    try {
      JSON.parse(t);
      return true;
    } catch {
      return false;
    }
  }
  return true;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Classify one `data:` payload. Returns null for the `[DONE]` sentinel and empty payloads. */
export function parseEventData(payload: string): ParsedEvent | null {
  const t = payload.trim();
  if (!t || t === "[DONE]") return null;
  let obj: unknown;
  try {
    obj = JSON.parse(t);
  } catch {
    return { kind: "text", text: payload };
  }
  if (!isRecord(obj)) return { kind: "text", text: String(obj) };

  if (typeof obj.error === "string" && !("token" in obj) && !("answer" in obj)) {
    return { kind: "error", message: obj.error };
  }
  if (isRecord(obj.step) && typeof obj.step.id === "string") {
    const st = obj.step.status;
    const status = st === "start" || st === "end" || st === "error" ? st : "start";
    return {
      kind: "step",
      step: {
        id: obj.step.id,
        status,
        label: typeof obj.step.label === "string" ? obj.step.label : undefined,
        t_ms: typeof obj.step.t_ms === "number" ? obj.step.t_ms : undefined,
        detail: isRecord(obj.step.detail) ? obj.step.detail : undefined,
      },
    };
  }
  if (isRecord(obj.progress)) {
    const p = obj.progress;
    return {
      kind: "progress",
      progress: {
        phase: typeof p.phase === "string" ? p.phase : "researching",
        done: typeof p.done === "number" ? p.done : 0,
        total: typeof p.total === "number" ? p.total : 0,
      },
    };
  }
  if (typeof obj.token === "string") return { kind: "token", token: obj.token };
  if (obj.done === true || typeof obj.answer === "string" || "verified" in obj || "citations" in obj) {
    return { kind: "done", data: obj };
  }
  return { kind: "unknown", data: obj };
}

/** Convenience: parse a whole string (tests / non-streaming use). */
export function parseSseText(text: string): ParsedEvent[] {
  const b = new SseBuffer();
  const payloads = [...b.push(text), ...b.end()];
  const events: ParsedEvent[] = [];
  for (const p of payloads) {
    const e = parseEventData(p);
    if (e) events.push(e);
  }
  return events;
}

/**
 * Read a fetch Response body as SSE and call `onEvent` for each parsed event.
 * Resolves when the stream closes; rejects only on network errors.
 */
export async function readSse(body: ReadableStream<Uint8Array>, onEvent: (e: ParsedEvent) => void, signal?: AbortSignal): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  const buf = new SseBuffer();
  const emit = (payloads: string[]) => {
    for (const p of payloads) {
      const e = parseEventData(p);
      if (e) onEvent(e);
    }
  };
  try {
    while (true) {
      if (signal?.aborted) break;
      const { done, value } = await reader.read();
      if (done) break;
      emit(buf.push(decoder.decode(value, { stream: true })));
    }
    emit(buf.push(decoder.decode()));
    emit(buf.end());
  } finally {
    try {
      reader.releaseLock();
    } catch {
      /* already released */
    }
  }
}

// ── Step timeline reducer ────────────────────────────────────────────────────

export interface TimelineStep {
  id: string;
  label: string;
  status: "running" | "done" | "error";
  t_ms?: number;
  detail?: Record<string, unknown>;
}

export interface StepLike {
  id: string;
  status: "start" | "end" | "error";
  label?: string;
  t_ms?: number;
  detail?: Record<string, unknown>;
}

/** Fold a step event into the ordered timeline (start adds, end/error updates). Unknown ids are kept. */
export function applyStep(steps: TimelineStep[], ev: StepLike): TimelineStep[] {
  const i = steps.findIndex((s) => s.id === ev.id);
  const status: TimelineStep["status"] = ev.status === "start" ? "running" : ev.status === "end" ? "done" : "error";
  if (i === -1) {
    return [...steps, { id: ev.id, label: ev.label || ev.id, status, t_ms: ev.t_ms, detail: ev.detail }];
  }
  const prev = steps[i];
  const next: TimelineStep = {
    id: prev.id,
    label: ev.label || prev.label,
    status,
    t_ms: ev.t_ms ?? prev.t_ms,
    detail: ev.detail ? { ...prev.detail, ...ev.detail } : prev.detail,
  };
  return [...steps.slice(0, i), next, ...steps.slice(i + 1)];
}
