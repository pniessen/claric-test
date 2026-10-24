// Offline tests: scoring logic, request validation, and the case runner with a fake model.
import { test } from "node:test";
import assert from "node:assert/strict";
import { runCheck } from "../src/evaluate.ts";
import { validateRequest, type ClairicAnswer, type ClairicResponse } from "../src/clairic.ts";
import { CASES, forecastTruth } from "../src/cases.ts";
import { runCase } from "../src/runner.ts";

const ans = (a: Partial<ClairicAnswer>): ClairicAnswer => ({ type: "text", value: null, ...a });

test("equals: numbers within half a cent, strings ignore case", () => {
  assert.equal(runCheck("x", { equals: 92 }, ans({ type: "number", value: 92.004 })).pass, true);
  assert.equal(runCheck("x", { equals: 92 }, ans({ type: "number", value: 92.01 })).pass, false);
  assert.equal(runCheck("x", { equals: 92 }, ans({ type: "number", value: "92.00" })).pass, true);
  assert.equal(runCheck("x", { equals: "inv-2291" }, ans({ value: " INV-2291 " })).pass, true);
});

test("isNull: passes on null or empty list, reports the guess", () => {
  const r = runCheck("t", { isNull: true }, ans({ value: null, guess: "1Z999", p_null: 0.93 }));
  assert.equal(r.pass, true);
  assert.match(r.got, /1Z999/);
  assert.equal(runCheck("t", { isNull: true }, ans({ type: "list", value: [] })).pass, true);
  assert.equal(runCheck("t", { isNull: true }, ans({ value: "1Z999" })).pass, false);
});

test("contains: matches text or any list item", () => {
  assert.equal(runCheck("d", { contains: "lamp" }, ans({ type: "list", value: ["Cracked LAMP base", "dent"] })).pass, true);
  assert.equal(runCheck("d", { contains: "lamp" }, ans({ type: "list", value: ["dent"] })).pass, false);
});

test("matches: regex on string values", () => {
  assert.equal(runCheck("t", { matches: "2026-11-03[T ]14:30" }, ans({ value: "2026-11-03T14:30:00" })).pass, true);
  assert.equal(runCheck("t", { matches: "^0?9:00" }, ans({ value: "09:00" })).pass, true);
  assert.equal(runCheck("t", { matches: "^0?9:00" }, ans({ value: "21:00" })).pass, false);
});

test("between: uses the median for estimates", () => {
  const est = ans({ type: "estimate", value: 415000, median: 415000, p10: 370000, p90: 470000 });
  assert.equal(runCheck("p", { between: [385000, 435000] }, est).pass, true);
  assert.equal(runCheck("p", { between: [420000, 435000] }, est).pass, false);
});

test("near: compares each forecast step", () => {
  const good = ans({ type: "forecast", value: [100, 210], median: [100, 210] });
  assert.equal(runCheck("f", { near: [100, 200], tolerance: 0.1 }, good).pass, true);
  const bad = ans({ type: "forecast", value: [100, 260], median: [100, 260] });
  assert.equal(runCheck("f", { near: [100, 200], tolerance: 0.1 }, bad).pass, false);
  const short = ans({ type: "forecast", value: [100], median: [100] });
  assert.equal(runCheck("f", { near: [100, 200], tolerance: 0.1 }, short).pass, false);
});

test("missing answer fails cleanly", () => {
  assert.equal(runCheck("x", { equals: 1 }, undefined).pass, false);
});

test("validateRequest catches common mistakes", () => {
  assert.deepEqual(validateRequest({ state: "hi", fields: { a: { type: "text" } } }), []);
  assert.ok(validateRequest({ state: "", fields: { a: { type: "text" } } }).length > 0);
  assert.ok(validateRequest({ state: "hi", fields: {} }).length > 0);
  assert.ok(validateRequest({ state: "hi", fields: { a: { type: "list" } } }).some((p) => p.includes("items")));
  assert.ok(validateRequest({ state: "hi", fields: { a: { type: "estimate" } } }).some((p) => p.includes("min")));
  assert.ok(validateRequest({ state: "hi", fields: { "1bad": { type: "text" } } }).length > 0);
});

test("every built-in case is a valid request and every check names a field", () => {
  for (const c of CASES) {
    const withImages = c.imageAssets?.length ? { ...c.request, images: ["data:image/png;base64,AA=="] } : c.request;
    assert.deepEqual(validateRequest(withImages), [], c.id);
    for (const field of Object.keys(c.expect ?? {})) {
      assert.ok(field in c.request.fields, `${c.id}: check for unknown field ${field}`);
    }
  }
});

test("forecast truth is 7 plausible daily values", () => {
  assert.equal(forecastTruth.length, 7);
  assert.ok(forecastTruth.every((v) => v > 500 && v < 2500));
});

test("runCase scores a fake model and reports errors", async () => {
  const c = CASES.find((x) => x.id === "null_handling")!;
  const fake = async (): Promise<ClairicResponse> => ({
    model: "clairic",
    truncated: false,
    usage: { input_tokens: 1000, output_tokens: 0 },
    answers: {
      refund_amount: { type: "number", value: 12.5, confidence: 0.98, p_null: 0.01 },
      tracking_number: { type: "text", value: null, guess: "x", p_null: 0.9 },
      carrier: { type: "text", value: "UPS", p_null: 0.4 },
      coupon_code: { type: "text", value: null, p_null: 0.99 },
    },
  });
  const r = await runCase(c, fake, async () => "");
  assert.equal(r.passed, 3);
  assert.equal(r.total, 4);
  assert.equal(r.ok, false);
  assert.equal(r.cost_usd, 0.0001);

  const failing = await runCase(c, async () => { throw new Error("boom"); }, async () => "");
  assert.equal(failing.error, "boom");
  assert.equal(failing.ok, false);
});
