import { CLAIRIC_MODEL, estimateCostUsd, validateRequest, type ClairicRequest, type ClairicResponse } from "./clairic.ts";
import { CASES, findCase } from "./cases.ts";
import { runCase, type CallModel, type LoadImage } from "./runner.ts";
import { PAGE_HTML } from "./page.ts";
import receiptPng from "../assets/receipt.png";

export interface Env {
  AI: Ai;
  /** Optional. When set, every /api route needs `Authorization: Bearer <token>`. */
  ACCESS_TOKEN?: string;
}

const ASSETS: Record<string, { bytes: ArrayBuffer; type: string }> = {
  "receipt.png": { bytes: receiptPng, type: "image/png" },
};

export default {
  async fetch(request, env): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;

    if (request.method === "GET" && path === "/") {
      return new Response(PAGE_HTML, { headers: { "content-type": "text/html; charset=utf-8" } });
    }
    if (request.method === "GET" && path === "/assets/receipt.png") {
      return new Response(ASSETS["receipt.png"]!.bytes, { headers: { "content-type": "image/png" } });
    }

    if (!path.startsWith("/api/")) return json({ error: "not found" }, 404);
    if (!(await authorized(request, env))) return json({ error: "unauthorized" }, 401);

    const call = modelCaller(env);

    // GET /api/cases — list built-in cases
    if (request.method === "GET" && path === "/api/cases") {
      return json(
        CASES.map((c) => ({
          id: c.id,
          title: c.title,
          about: c.about,
          request: c.request,
          imageAssets: c.imageAssets ?? [],
          expect: c.expect ?? {},
        })),
      );
    }

    // POST /api/cases/run-all — run every case one after another (keeps latency numbers clean)
    if (request.method === "POST" && path === "/api/cases/run-all") {
      const results = [];
      for (const c of CASES) results.push(await runCase(c, call, loadAsset));
      const checks = results.flatMap((r) => r.checks);
      return json({
        model: CLAIRIC_MODEL,
        cases_passed: results.filter((r) => r.ok).length,
        cases_total: results.length,
        checks_passed: checks.filter((c) => c.pass).length,
        checks_total: checks.length,
        total_cost_usd: results.reduce((s, r) => s + (r.cost_usd ?? 0), 0),
        results,
      });
    }

    // POST /api/cases/:id/run — run one case
    const m = path.match(/^\/api\/cases\/([A-Za-z0-9_-]+)\/run$/);
    if (request.method === "POST" && m) {
      const c = findCase(m[1]!);
      if (!c) return json({ error: `unknown case: ${m[1]}` }, 404);
      return json(await runCase(c, call, loadAsset));
    }

    // POST /api/extract — send your own request (same body as the Clairic API)
    if (request.method === "POST" && path === "/api/extract") {
      let body: ClairicRequest;
      try {
        body = (await request.json()) as ClairicRequest;
      } catch {
        return json({ error: "body must be JSON" }, 400);
      }
      const problems = validateRequest(body);
      if (problems.length) return json({ error: "invalid request", problems }, 400);
      const started = Date.now();
      try {
        const response = await call({ ...body, model: CLAIRIC_MODEL });
        return json({
          latency_ms: Date.now() - started,
          cost_usd: response.usage ? estimateCostUsd(response.usage) : undefined,
          response,
        });
      } catch (err) {
        return json({ error: err instanceof Error ? err.message : String(err), latency_ms: Date.now() - started }, 502);
      }
    }

    return json({ error: "not found" }, 404);
  },
} satisfies ExportedHandler<Env>;

function modelCaller(env: Env): CallModel {
  // Clairic may be missing from the installed Ai type's model list, so call it untyped.
  const ai = env.AI as unknown as { run(model: string, input: unknown): Promise<unknown> };
  return async (req) => (await ai.run(CLAIRIC_MODEL, req)) as ClairicResponse;
}

const loadAsset: LoadImage = async (name) => {
  const asset = ASSETS[name];
  if (!asset) throw new Error(`unknown asset ${name}`);
  // Data URL rather than { content_type, base64 }: both are in Clairic's schema, but Clef (same API
  // family) rejects the object form with a 422, so use the form known to work.
  return `data:${asset.type};base64,${toBase64(asset.bytes)}`;
};

function toBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(bin);
}

async function authorized(request: Request, env: Env): Promise<boolean> {
  if (!env.ACCESS_TOKEN) return true;
  const header = request.headers.get("authorization") ?? "";
  const given = header.startsWith("Bearer ") ? header.slice(7) : "";
  const enc = new TextEncoder();
  // Hash both sides so the comparison is constant-time and same-length.
  const [a, b] = await Promise.all([
    crypto.subtle.digest("SHA-256", enc.encode(given)),
    crypto.subtle.digest("SHA-256", enc.encode(env.ACCESS_TOKEN)),
  ]);
  return crypto.subtle.timingSafeEqual(a, b);
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}
