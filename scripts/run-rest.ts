// Runs the built-in cases against the Workers AI REST API, with no Worker deploy needed.
//
//   npm run cases
//   npm run cases -- email_complaint receipt_image   # just these cases
//   npm run cases -- --json                          # full JSON instead of a table
//
// Credentials:
//   account ID  CLOUDFLARE_ACCOUNT_ID, or CF_ACCOUNT_ID
//   API token   CLOUDFLARE_API_TOKEN, or on macOS a login-keychain entry named by CF_KEYCHAIN_SERVICE
// The token needs the "Workers AI: Read" permission. Requires Node 22.18+ (runs .ts directly).
import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { CLAIRIC_MODEL, type ClairicResponse } from "../src/clairic.ts";
import { CASES } from "../src/cases.ts";
import { runCase, type CallModel, type CaseResult, type LoadImage } from "../src/runner.ts";

const accountId = process.env.CLOUDFLARE_ACCOUNT_ID || process.env.CF_ACCOUNT_ID;
const apiToken = process.env.CLOUDFLARE_API_TOKEN || keychainToken();
if (!accountId || !apiToken) {
  console.error(
    "Need a Cloudflare account ID (CLOUDFLARE_ACCOUNT_ID or CF_ACCOUNT_ID) and an API token\n" +
      "(CLOUDFLARE_API_TOKEN, or on macOS a keychain entry named by CF_KEYCHAIN_SERVICE).",
  );
  process.exit(2);
}

function keychainToken(): string | undefined {
  const service = process.env.CF_KEYCHAIN_SERVICE;
  if (process.platform !== "darwin" || !service) return undefined;
  try {
    const out = execFileSync(
      "security",
      ["find-generic-password", "-a", process.env.USER ?? "", "-s", service, "-w"],
      { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] },
    );
    return out.trim() || undefined;
  } catch {
    return undefined;
  }
}

const args = process.argv.slice(2);
const asJson = args.includes("--json");
const wanted = args.filter((a) => !a.startsWith("--"));
const unknown = wanted.filter((id) => !CASES.some((c) => c.id === id));
if (unknown.length) {
  console.error(`Unknown case(s): ${unknown.join(", ")}\nAvailable: ${CASES.map((c) => c.id).join(", ")}`);
  process.exit(2);
}
const selected = wanted.length ? CASES.filter((c) => wanted.includes(c.id)) : CASES;

const endpoint = `https://api.cloudflare.com/client/v4/accounts/${accountId}/ai/run/${CLAIRIC_MODEL}`;

const call: CallModel = async (req) => {
  const res = await fetch(endpoint, {
    method: "POST",
    headers: { authorization: `Bearer ${apiToken}`, "content-type": "application/json" },
    body: JSON.stringify(req),
  });
  const body = (await res.json().catch(() => null)) as
    | { success?: boolean; result?: ClairicResponse; errors?: { message: string }[] }
    | null;
  if (!res.ok || !body?.success || !body.result) {
    const msg = body?.errors?.map((e) => e.message).join("; ") || `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return body.result;
};

const loadImage: LoadImage = async (name) => {
  const bytes = await readFile(new URL(`../assets/${name}`, import.meta.url));
  // Data URL, not { content_type, base64 }: Clef rejects anything but data URLs (a 422),
  // and Clairic's schema accepts both, so use the form known to work.
  return `data:image/png;base64,${bytes.toString("base64")}`;
};

const results: CaseResult[] = [];
for (const c of selected) {
  const r = await runCase(c, call, loadImage);
  results.push(r);
  if (!asJson) printResult(r);
}

const checks = results.flatMap((r) => r.checks);
const summary = {
  cases_passed: results.filter((r) => r.ok).length,
  cases_total: results.length,
  checks_passed: checks.filter((c) => c.pass).length,
  checks_total: checks.length,
  total_cost_usd: results.reduce((s, r) => s + (r.cost_usd ?? 0), 0),
};

if (asJson) {
  console.log(JSON.stringify({ model: CLAIRIC_MODEL, ...summary, results }, null, 2));
} else {
  console.log(
    `\n${summary.cases_passed}/${summary.cases_total} cases, ${summary.checks_passed}/${summary.checks_total} checks passed` +
      ` · total cost $${summary.total_cost_usd.toFixed(6)}`,
  );
}
process.exitCode = results.some((r) => r.error) ? 1 : 0;

function printResult(r: CaseResult) {
  const mark = r.error ? "ERROR" : r.ok ? "PASS " : "FAIL ";
  const tokens = r.response?.usage?.input_tokens ?? "?";
  console.log(`\n${mark} ${r.id} — ${r.title}  (${r.latency_ms} ms, ${tokens} input tokens)`);
  if (r.error) {
    console.log(`  ${r.error}`);
    return;
  }
  for (const c of r.checks) {
    console.log(`  ${c.pass ? "✓" : "✗"} ${c.field.padEnd(20)} expected ${c.expected}; got ${c.got}`);
  }
}
