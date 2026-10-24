#!/usr/bin/env python3
"""Run comparison systems on the same receipt images and answer keys as clairic_bench.py.

    python3 bench/baselines.py textract sroie        # AWS Textract AnalyzeExpense
    python3 bench/baselines.py haiku-5-5 cord        # Claude Haiku 5.5, structured JSON output
    python3 bench/baselines.py haiku-4-5 sroie --limit 5
    python3 bench/baselines.py textract cord --score # re-score saved answers

Answers go to bench/results/<system>_<dataset>.jsonl in Clairic's answer format, so the same scorer
applies. Summary: bench/reports/<system>_<dataset>.json.

Credentials:
  textract   AWS_REGION (default us-east-1) and the standard AWS credential chain, or
             TEXTRACT_AWS_ACCESS_KEY_ID / TEXTRACT_AWS_SECRET_ACCESS_KEY to use a separate key.
             Needs textract:AnalyzeExpense. $0.01 per page.
  haiku-*    BENCH_ANTHROPIC_API_KEY (Claude Code sessions withhold ANTHROPIC_API_KEY), or ANTHROPIC_API_KEY.
"""
import base64, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clairic_bench as cb

HERE = cb.HERE
TEXTRACT_PRICE_PER_PAGE = 0.01
HAIKU = {  # model id, $ per million input / output tokens
    "haiku-5-5": ("claude-haiku-5-5", 0.10, 0.50),
    "haiku-4-5": ("claude-haiku-4-5", 1.00, 5.00),
}


# ---------------------------------------------------------------- Textract
def textract_client():
    import boto3
    region = os.environ.get("TEXTRACT_AWS_REGION") or os.environ.get("AWS_REGION") or "us-east-1"
    key, secret = os.environ.get("TEXTRACT_AWS_ACCESS_KEY_ID"), os.environ.get("TEXTRACT_AWS_SECRET_ACCESS_KEY")
    if key and secret:
        return boto3.client("textract", region_name=region, aws_access_key_id=key, aws_secret_access_key=secret)
    return boto3.client("textract", region_name=region)


def textract_fields(resp):
    """Best (highest-confidence) summary field per Textract type, plus OTHER fields by label, plus items."""
    best, other, items = {}, [], []
    for doc in resp.get("ExpenseDocuments", []):
        for f in doc.get("SummaryFields", []):
            typ = f.get("Type", {}).get("Text", "")
            val = f.get("ValueDetection", {})
            if not val.get("Text"):
                continue
            entry = (val["Text"], val.get("Confidence", 0) / 100, (f.get("LabelDetection") or {}).get("Text", ""))
            if typ == "OTHER":
                other.append(entry)
            elif typ not in best or entry[1] > best[typ][1]:
                best[typ] = entry
        for g in doc.get("LineItemGroups", []):
            for li in g.get("LineItems", []):
                for f in li.get("LineItemExpenseFields", []):
                    if f.get("Type", {}).get("Text") == "ITEM" and f.get("ValueDetection", {}).get("Text"):
                        items.append(f["ValueDetection"]["Text"])
    return best, other, items


def by_label(other, pattern):
    hits = [e for e in other if re.search(pattern, e[2], re.I)]
    return max(hits, key=lambda e: e[1]) if hits else None


def textract_answers(name, resp):
    best, other, items = textract_fields(resp)

    def ans(entry, convert=lambda t: t):
        if not entry:
            return {"type": "textract", "value": None}
        try:
            v = convert(entry[0])
        except (TypeError, ValueError):
            v = None
        return {"type": "textract", "value": v, "confidence": entry[1], "raw": entry[0]}

    if name == "sroie":
        return {
            "company": ans(best.get("VENDOR_NAME") or best.get("NAME")),
            "address": ans(best.get("VENDOR_ADDRESS") or best.get("ADDRESS")),
            "date": ans(best.get("INVOICE_RECEIPT_DATE"), lambda t: cb.gt_date(t) or t),
            "total": ans(best.get("TOTAL"), cb.num),
        }
    return {
        "total_price": ans(best.get("TOTAL"), cb.idr),
        "subtotal_price": ans(best.get("SUBTOTAL"), cb.idr),
        "tax_price": ans(best.get("TAX"), cb.idr),
        "service_price": ans(best.get("SERVICE_CHARGE"), cb.idr),
        "cash_price": ans(by_label(other, r"tunai|cash") or best.get("AMOUNT_PAID"), cb.idr),
        "change_price": ans(by_label(other, r"kembali|change"), cb.idr),
        "item_quantity": ans(by_label(other, r"qty|quantity|item"), cb.num),
        "menu_items": {"type": "list", "value": items or None},
    }


def make_textract(name):
    client = textract_client()

    def one(doc):
        kind, content = doc
        if kind != "images" or len(content) != 1:
            raise ValueError("Textract AnalyzeExpense here takes one receipt image")
        t0 = time.time()
        resp = client.analyze_expense(Document={"Bytes": content[0]})
        ms = (time.time() - t0) * 1000
        return {"answers": textract_answers(name, resp), "usage": {"pages": 1}}, ms, TEXTRACT_PRICE_PER_PAGE
    return one


# ---------------------------------------------------------------- Claude Haiku
JSON_TYPES = {"text": "string", "date": "string", "number": "number", "duration": "number"}


def haiku_schema(fields):
    props = {}
    for f, spec in fields.items():
        if spec["type"] == "list":
            value = {"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]}
        else:
            value = {"anyOf": [{"type": JSON_TYPES[spec["type"]]}, {"type": "null"}]}
        props[f] = {"type": "object", "properties": {"value": value, "confidence": {"type": "number"}},
                    "required": ["value", "confidence"], "additionalProperties": False}
    return {"type": "object", "properties": props, "required": list(fields), "additionalProperties": False}


def haiku_prompt(fields):
    lines = []
    for f, spec in fields.items():
        kind = {"date": "date as YYYY-MM-DD", "number": "number", "list": "list of strings",
                "duration": f"number of {spec.get('unit', 'units')}"}.get(spec["type"], "text")
        lines.append(f"- {f} ({kind}): {spec.get('instructions', '')}".rstrip(": "))
    return ("Extract these fields from the document.\n" + "\n".join(lines) +
            "\n\nFor each field give `value`, or null if the receipt does not show it (do not guess), and "
            "`confidence`: your probability from 0 to 1 that the value is correct. Copy text as printed. "
            "Give numbers as plain numbers without currency symbols or thousands separators.")


def make_haiku(system, name):
    import anthropic
    model, p_in, p_out = HAIKU[system]
    # Claude Code cloud sessions reserve ANTHROPIC_API_KEY and set ANTHROPIC_BASE_URL for their own use,
    # so read the key from BENCH_ANTHROPIC_API_KEY there and always call the public API directly.
    key = os.environ.get("BENCH_ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("need BENCH_ANTHROPIC_API_KEY (or ANTHROPIC_API_KEY outside Claude Code)")
    client = anthropic.Anthropic(api_key=key, base_url="https://api.anthropic.com")
    fields = cb.DATASETS[name]["fields"]
    schema, prompt = haiku_schema(fields), haiku_prompt(fields)
    extra = {"output_config": {"format": {"type": "json_schema", "schema": schema}}}
    if system == "haiku-5-5":   # thinking is on by default; extraction doesn't need much of it
        extra["output_config"]["effort"] = "low"

    def one(doc):
        kind, content = doc
        if kind == "text":
            parts = [{"type": "text", "text": f"<document>\n{content}\n</document>"}]
        else:
            parts = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                  "data": base64.standard_b64encode(b).decode()}} for b in content]
        t0 = time.time()
        msg = client.messages.create(
            model=model, max_tokens=4000, **extra,
            messages=[{"role": "user", "content": parts + [{"type": "text", "text": prompt}]}])
        ms = (time.time() - t0) * 1000
        if msg.stop_reason not in ("end_turn", "stop_sequence"):
            raise RuntimeError(f"stop_reason {msg.stop_reason}")
        data = json.loads(next(b.text for b in msg.content if b.type == "text"))
        answers = {}
        for f, spec in fields.items():
            a = data.get(f) or {}
            answers[f] = {"type": spec["type"], "value": a.get("value"), "confidence": a.get("confidence")}
            if spec["type"] == "list":
                answers[f].pop("confidence")
        u = msg.usage
        cost = u.input_tokens / 1e6 * p_in + u.output_tokens / 1e6 * p_out
        return {"answers": answers, "usage": {"input_tokens": u.input_tokens,
                                              "output_tokens": u.output_tokens}}, ms, cost
    return one


# ---------------------------------------------------------------- runner
def run(system, name, docs, workers):
    one = make_textract(name) if system == "textract" else make_haiku(system, name)
    out = HERE / "results" / f"{system}_{name}.jsonl"
    out.parent.mkdir(exist_ok=True)
    done = {}
    if out.exists():
        for l in out.read_text().splitlines():
            r = json.loads(l)
            if r.get("response"):
                done[r["idx"]] = r
    todo = [d for d in docs if d["idx"] not in done]
    print(f"{system} on {name}: {len(done)} saved, {len(todo)} to run", flush=True)

    def call(d):
        base = HERE / "data" / name
        if "text" in d:
            doc = ("text", (base / d["text"]).read_text(encoding="utf-8"))
        else:
            doc = ("images", [(base / f).read_bytes() for f in d.get("images") or [d["image"]]])
        err = None
        for attempt in range(4):
            try:
                resp, ms, cost = one(doc)
                return {"idx": d["idx"], "latency_ms": ms, "response": resp, "cost_usd": cost, "error": None}
            except Exception as e:   # throttling and transient errors: back off and retry
                err = f"{type(e).__name__}: {str(e)[:300]}"
                if any(s in err for s in ("AccessDenied", "UnrecognizedClient", "InvalidClientTokenId",
                                          "AuthenticationError", "PermissionDenied", "NotFoundError")):
                    break
                time.sleep(2 * (attempt + 1))
        return {"idx": d["idx"], "latency_ms": None, "response": None, "error": err}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, r in enumerate(ex.map(call, todo), 1):
            done[r["idx"]] = r
            if r["error"]:
                print(f"  {r['idx']}: {r['error']}", flush=True)
            if i % 25 == 0:
                print(f"  {i}/{len(todo)}", flush=True)
    out.write_text("\n".join(json.dumps(done[k], ensure_ascii=False) for k in sorted(done)))
    return done


def main():
    args = sys.argv[1:]
    if len(args) < 2 or args[0] not in ("textract", *HAIKU) or args[1] not in cb.DATASETS:
        sys.exit(__doc__)
    system, name = args[0], args[1]
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None
    workers = int(args[args.index("--workers") + 1]) if "--workers" in args else 4
    docs = cb.fetch(name)[:limit] if limit else cb.fetch(name)
    if "--score" in args:
        p = HERE / "results" / f"{system}_{name}.jsonl"
        results = {json.loads(l)["idx"]: json.loads(l) for l in p.read_text().splitlines()} if p.exists() else {}
    else:
        results = run(system, name, docs, workers)
    rep = cb.score(name, docs, results, system=system)
    cb.show(rep)
    if not limit:
        (HERE / "reports").mkdir(exist_ok=True)
        (HERE / "reports" / f"{system}_{name}.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
