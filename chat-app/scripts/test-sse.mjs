// Run from chat-app/: node --experimental-strip-types scripts/test-sse.mjs
import assert from "node:assert/strict";
import { SseBuffer, parseSseText, parseEventData, applyStep } from "../src/lib/sse.ts";
import { SSE_QUERY_STREAM, SSE_JOB_EVENTS } from "../src/lib/__fixtures__/contracts.ts";

let n = 0;
const t = (name, fn) => {
  fn();
  n++;
  console.log("ok -", name);
};

t("parses full query stream in order", () => {
  const evs = parseSseText(SSE_QUERY_STREAM);
  assert.deepEqual(evs.map((e) => e.kind), ["step", "step", "step", "step", "step", "token", "token", "done"]);
  assert.equal(evs.at(-1).data.trace_id, "tr_1");
});

t("identical result when fed 1 character at a time", () => {
  const b = new SseBuffer();
  const payloads = [];
  for (const ch of SSE_QUERY_STREAM) payloads.push(...b.push(ch));
  payloads.push(...b.end());
  const kinds = payloads.map(parseEventData).filter(Boolean).map((e) => e.kind);
  assert.deepEqual(kinds, parseSseText(SSE_QUERY_STREAM).map((e) => e.kind));
});

t("CRLF and events without blank-line separators", () => {
  const evs = parseSseText('data: {"token":"a"}\r\ndata: {"token":"b"}\r\n');
  assert.deepEqual(evs.map((e) => e.token), ["a", "b"]);
});

t("unknown step ids kept, bad status defaults, missing fields ok", () => {
  const e = parseEventData('{"step":{"id":"zzz","status":"weird"}}');
  assert.equal(e.kind, "step");
  assert.equal(e.step.status, "start");
});

t("malformed json becomes text, never throws; DONE/empty -> null", () => {
  assert.equal(parseEventData("{oops").kind, "text");
  assert.equal(parseEventData("[DONE]"), null);
  assert.equal(parseEventData(""), null);
});

t("error event", () => {
  assert.deepEqual(parseEventData('{"error":"RAG stream server unavailable"}'), { kind: "error", message: "RAG stream server unavailable" });
});

t("job events: step, progress, done", () => {
  assert.deepEqual(parseSseText(SSE_JOB_EVENTS).map((e) => e.kind), ["step", "progress", "done"]);
});

t("applyStep builds timeline and keeps unknown ids", () => {
  let tl = [];
  for (const e of parseSseText(SSE_QUERY_STREAM)) if (e.kind === "step") tl = applyStep(tl, e.step);
  assert.deepEqual(tl.map((s) => [s.id, s.status]), [["cache", "done"], ["mystery_future_step", "running"], ["retrieving", "done"]]);
  assert.equal(tl[2].detail.n, 12);
});

console.log(`${n} tests passed`);
