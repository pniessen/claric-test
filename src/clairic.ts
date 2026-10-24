// Types for the Clairic request/response, taken from the published JSON schemas:
// https://developers.cloudflare.com/workers-ai/models/clairic/schema-input.json
// https://developers.cloudflare.com/workers-ai/models/clairic/schema-output.json

export const CLAIRIC_MODEL = "@cf/cloudflare/clairic";

export type FieldType =
  | "text"
  | "number"
  | "date"
  | "time"
  | "datetime"
  | "duration"
  | "list"
  | "estimate"
  | "forecast";

export interface FieldSpec {
  type: FieldType;
  instructions?: string;
  /** Unit label. For durations: seconds | minutes | hours | days | weeks | months | years. */
  unit?: string;
  /** Numbers and durations only. */
  integer?: boolean;
  /** 0–6. Numbers, durations and number lists only. */
  decimals?: number;
  /** Required for list fields. */
  items?: "text" | "number" | "date";
  /** Required for estimate/forecast. A number, or an ISO date for date estimates. */
  min?: number | string;
  max?: number | string;
  /** Estimate grid: a positive number (days for date estimates), or "month" / "year". */
  step?: number | "month" | "year";
  scale?: "linear" | "log";
  /** Probability of null above which the value is null (default 0.5). */
  null_threshold?: number;
  /** Forecast only: 1–64 steps. */
  horizon?: number;
  interval?: "step" | "minute" | "hour" | "day" | "week" | "month" | "quarter" | "year";
}

/** A data URL string, or an object with a MIME type and base64 body. */
export type ImageInput = string | { content_type: string; base64: string };

export interface ClairicRequest {
  model?: "clairic" | "@cf/cloudflare/clairic";
  state?: string;
  fields: Record<string, FieldSpec>;
  /** Up to 4 PNG/JPEG/WebP images; 4 MiB each, 8 MiB total. */
  images?: ImageInput[];
  /** ISO date that anchors dates written without a year. */
  reference_date?: string;
  options?: {
    include_quantiles?: boolean;
    return_state_text?: boolean;
  };
}

export interface ClairicAnswer {
  type: string;
  value: unknown;
  guess?: unknown;
  p_null?: number;
  confidence?: number;
  span?: [number, number];
  spans?: [number, number][];
  mean?: unknown;
  median?: unknown;
  p10?: unknown;
  p90?: unknown;
  p_below?: number;
  p_above?: number;
  quantiles?: unknown[];
  steps?: unknown[];
}

export interface ClairicResponse {
  model: string;
  answers: Record<string, ClairicAnswer>;
  truncated: boolean;
  state_text?: string;
  usage: {
    state_tokens?: number;
    image_tokens?: number;
    schema_tokens?: number;
    input_tokens: number;
    output_tokens: number;
    passes?: number;
  };
}

/** Price per the model page: $0.10 per million input tokens, no output charge. */
export const USD_PER_MILLION_INPUT_TOKENS = 0.1;

export function estimateCostUsd(usage: ClairicResponse["usage"]): number {
  return (usage.input_tokens / 1_000_000) * USD_PER_MILLION_INPUT_TOKENS;
}

/**
 * Basic checks before sending, so obvious mistakes get a readable error
 * instead of a model-side 400. Returns a list of problems (empty = OK).
 */
export function validateRequest(req: ClairicRequest): string[] {
  const problems: string[] = [];
  const fields = req.fields ?? {};
  const names = Object.keys(fields);
  if (names.length === 0) problems.push("fields: at least one field is required");
  if (!req.state && !(req.images && req.images.length)) {
    problems.push("state: must be non-empty unless images are sent");
  }
  if (req.images && req.images.length > 4) problems.push("images: at most 4 images");
  for (const name of names) {
    if (!/^[A-Za-z_][A-Za-z0-9_.\-]{0,63}$/.test(name)) {
      problems.push(`fields.${name}: invalid field name`);
    }
    const f = fields[name];
    if (!f || typeof f !== "object" || !f.type) {
      problems.push(`fields.${name}: missing type`);
      continue;
    }
    if (f.type === "list" && !f.items) problems.push(`fields.${name}: list fields need "items"`);
    if ((f.type === "estimate" || f.type === "forecast") && (f.min === undefined || f.max === undefined)) {
      problems.push(`fields.${name}: ${f.type} fields need "min" and "max"`);
    }
    if (f.type === "forecast" && f.horizon !== undefined && (f.horizon < 1 || f.horizon > 64)) {
      problems.push(`fields.${name}: horizon must be 1–64`);
    }
  }
  return problems;
}
