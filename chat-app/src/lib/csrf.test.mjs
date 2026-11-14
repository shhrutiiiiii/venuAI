// Run: npm run test:csrf  (node's built-in runner; csrf.ts has no imports)
import test from "node:test";
import assert from "node:assert/strict";
import { csrfViolation } from "./csrf.ts";

const H = (o) => new Headers(o);
const HOST = "app.example.com";
const OK = { "sec-fetch-site": "same-origin", "x-requested-with": "venuai" };

test("safe methods are never blocked", () => {
  for (const m of ["GET", "HEAD", "OPTIONS"]) assert.equal(csrfViolation(m, H({}), HOST), null);
});

test("same-origin POST with the custom header passes", () => {
  assert.equal(csrfViolation("POST", H(OK), HOST), null);
});

test("POST without the custom header is blocked", () => {
  assert.ok(csrfViolation("POST", H({ "sec-fetch-site": "same-origin" }), HOST));
});

test("cross-site / same-site / none Sec-Fetch-Site is blocked even with the header", () => {
  for (const v of ["cross-site", "same-site", "none"]) {
    assert.ok(csrfViolation("POST", H({ ...OK, "sec-fetch-site": v }), HOST), v);
  }
});

test("foreign Origin is blocked, matching Origin passes (browsers without Fetch-Metadata)", () => {
  const base = { "x-requested-with": "venuai" };
  assert.ok(csrfViolation("DELETE", H({ ...base, origin: "https://evil.example" }), HOST));
  assert.ok(csrfViolation("DELETE", H({ ...base, origin: "not a url" }), HOST));
  assert.equal(csrfViolation("DELETE", H({ ...base, origin: `https://${HOST}` }), HOST), null);
});

test("no Origin and no Fetch-Metadata (non-browser) is blocked", () => {
  assert.ok(csrfViolation("PUT", H({ "x-requested-with": "venuai" }), HOST));
});

test("header with wrong value is blocked", () => {
  assert.ok(csrfViolation("POST", H({ ...OK, "x-requested-with": "XMLHttpRequest" }), HOST));
});
