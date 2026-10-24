// Runs one test case and scores it. Shared by the Worker and the command-line script,
// which differ only in how they call the model and load image files.
import {
  CLAIRIC_MODEL,
  estimateCostUsd,
  type ClairicRequest,
  type ClairicResponse,
  type ImageInput,
} from "./clairic.ts";
import { evaluate, type CheckResult } from "./evaluate.ts";
import type { TestCase } from "./cases.ts";

export type CallModel = (req: ClairicRequest) => Promise<ClairicResponse>;
export type LoadImage = (assetName: string) => Promise<ImageInput>;

export interface CaseResult {
  id: string;
  title: string;
  ok: boolean;
  latency_ms: number;
  passed: number;
  total: number;
  checks: CheckResult[];
  cost_usd?: number;
  response?: ClairicResponse;
  error?: string;
}

export function buildRequest(c: TestCase, images: ImageInput[]): ClairicRequest {
  const req: ClairicRequest = { model: CLAIRIC_MODEL, ...c.request };
  if (images.length) req.images = images;
  return req;
}

export async function runCase(c: TestCase, call: CallModel, loadImage: LoadImage): Promise<CaseResult> {
  const base = { id: c.id, title: c.title };
  let images: ImageInput[];
  try {
    images = await Promise.all((c.imageAssets ?? []).map(loadImage));
  } catch (err) {
    return { ...base, ok: false, latency_ms: 0, passed: 0, total: 0, checks: [], error: `image load: ${String(err)}` };
  }

  const started = Date.now();
  let response: ClairicResponse;
  try {
    response = await call(buildRequest(c, images));
  } catch (err) {
    const latency_ms = Date.now() - started;
    return { ...base, ok: false, latency_ms, passed: 0, total: 0, checks: [], error: errorText(err) };
  }
  const latency_ms = Date.now() - started;

  const checks = evaluate(c.expect, response.answers ?? {});
  const passed = checks.filter((x) => x.pass).length;
  return {
    ...base,
    ok: passed === checks.length,
    latency_ms,
    passed,
    total: checks.length,
    checks,
    cost_usd: response.usage ? estimateCostUsd(response.usage) : undefined,
    response,
  };
}

function errorText(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}
