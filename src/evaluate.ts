// Compare Clairic answers against what each test case expects.
import type { ClairicAnswer } from "./clairic.ts";

export type Check =
  /** Exact match. Numbers match within 0.005; strings ignore case and surrounding space. */
  | { equals: unknown }
  /** The model should say the value is not in the input. */
  | { isNull: true }
  /** Text (or any list item) contains this substring, ignoring case. */
  | { contains: string }
  /** String value matches this regex source. */
  | { matches: string }
  /** Number, estimate median, or every forecast median falls in [lo, hi]. */
  | { between: [number, number] }
  /** Within a relative tolerance of a target (or one target per forecast step). */
  | { near: number | number[]; tolerance: number };

export interface CheckResult {
  field: string;
  pass: boolean;
  expected: string;
  got: string;
}

const NUM_TOLERANCE = 0.005;

/** The single number to judge for a field: value for scalars, median for estimates. */
function numericValue(a: ClairicAnswer): number | null {
  const v = a.type === "estimate" ? (a.median ?? a.value) : a.value;
  if (typeof v === "number") return v;
  if (typeof v === "string" && v.trim() !== "" && !Number.isNaN(Number(v))) return Number(v);
  return null;
}

/** Per-step medians for forecasts. Falls back to value when median is missing. */
function forecastMedians(a: ClairicAnswer): number[] | null {
  const m = Array.isArray(a.median) ? a.median : Array.isArray(a.value) ? a.value : null;
  if (!m || !m.every((x) => typeof x === "number")) return null;
  return m as number[];
}

function show(v: unknown): string {
  if (v === undefined) return "(missing)";
  return JSON.stringify(v);
}

function describe(check: Check): string {
  if ("equals" in check) return `= ${show(check.equals)}`;
  if ("isNull" in check) return "null";
  if ("contains" in check) return `contains ${show(check.contains)}`;
  if ("matches" in check) return `matches /${check.matches}/`;
  if ("between" in check) return `in [${check.between[0]}, ${check.between[1]}]`;
  const t = Array.isArray(check.near) ? `[${check.near.join(", ")}]` : String(check.near);
  return `≈ ${t} ±${Math.round(check.tolerance * 100)}%`;
}

function valuesEqual(got: unknown, want: unknown): boolean {
  if (typeof want === "number") {
    const n = typeof got === "number" ? got : typeof got === "string" ? Number(got) : NaN;
    return Number.isFinite(n) && Math.abs(n - want) <= NUM_TOLERANCE;
  }
  if (typeof want === "string" && typeof got === "string") {
    return got.trim().toLowerCase() === want.trim().toLowerCase();
  }
  return JSON.stringify(got) === JSON.stringify(want);
}

export function runCheck(field: string, check: Check, answer: ClairicAnswer | undefined): CheckResult {
  const expected = describe(check);
  if (!answer) return { field, pass: false, expected, got: "(no answer returned)" };

  const value = answer.value;
  const gotStr =
    answer.type === "estimate"
      ? `median ${show(answer.median)} (p10 ${show(answer.p10)}, p90 ${show(answer.p90)})`
      : answer.type === "forecast"
        ? `median ${show(answer.median ?? value)}`
        : show(value);

  let pass = false;
  if ("isNull" in check) {
    pass = value === null || (Array.isArray(value) && value.length === 0);
    if (!pass) return { field, pass, expected, got: gotStr };
    return { field, pass, expected, got: `null (guess ${show(answer.guess)}, p_null ${show(answer.p_null)})` };
  } else if ("equals" in check) {
    pass = valuesEqual(value, check.equals);
  } else if ("contains" in check) {
    const needle = check.contains.toLowerCase();
    const items = Array.isArray(value) ? value : [value];
    pass = items.some((x) => typeof x === "string" && x.toLowerCase().includes(needle));
  } else if ("matches" in check) {
    pass = typeof value === "string" && new RegExp(check.matches, "i").test(value);
  } else if ("between" in check) {
    const [lo, hi] = check.between;
    if (answer.type === "forecast") {
      const meds = forecastMedians(answer);
      pass = !!meds && meds.length > 0 && meds.every((m) => m >= lo && m <= hi);
    } else {
      const n = numericValue(answer);
      pass = n !== null && n >= lo && n <= hi;
    }
  } else if ("near" in check) {
    const targets = Array.isArray(check.near) ? check.near : [check.near];
    const within = (got: number, want: number) => Math.abs(got - want) <= Math.abs(want) * check.tolerance;
    if (answer.type === "forecast") {
      const meds = forecastMedians(answer);
      pass = !!meds && meds.length === targets.length && meds.every((m, i) => within(m, targets[i]!));
    } else {
      const n = numericValue(answer);
      pass = n !== null && within(n, targets[0]!);
    }
  }
  return { field, pass, expected, got: gotStr };
}

export function evaluate(
  expect: Record<string, Check> | undefined,
  answers: Record<string, ClairicAnswer>,
): CheckResult[] {
  if (!expect) return [];
  return Object.entries(expect).map(([field, check]) => runCheck(field, check, answers[field]));
}
