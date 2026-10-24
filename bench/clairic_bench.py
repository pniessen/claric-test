#!/usr/bin/env python3
"""Benchmark Clairic on public receipt datasets with answer keys.

    python3 bench/clairic_bench.py sroie            # 347 scanned receipts: company, date, address, total
    python3 bench/clairic_bench.py cord             # 100 receipt photos: totals, tax, cash, change, items
    python3 bench/clairic_bench.py nda              # 83 Kleister NDAs (text): effective date, jurisdiction, parties, term
    python3 bench/clairic_bench.py charity          # 440 Kleister charity reports (text): income, spending, dates, address
    python3 bench/clairic_bench.py sbc_text         # 150 health-plan SBCs as text, scored against CMS 2026 data
    python3 bench/clairic_bench.py sbc_image        # the same SBCs as page images (pages 1-3 + coverage examples)
    python3 bench/clairic_bench.py ma_text          # 100 Humana 2027 Medicare Advantage plans, scored against CMS PBP
    python3 bench/clairic_bench.py ma_image         # the same plans' benefit-table and drug pages as images
    python3 bench/clairic_bench.py cord --limit 10  # quick check
    python3 bench/clairic_bench.py sroie --score    # re-score saved answers without calling the model

Images are downloaded once into bench/data/, answers saved to bench/results/<dataset>.jsonl (a rerun only
retries missing or failed images), and the summary goes to bench/reports/<dataset>.json.
Credentials: CLOUDFLARE_ACCOUNT_ID (or CF_ACCOUNT_ID) and CLOUDFLARE_API_TOKEN, or on macOS a login-keychain
entry named by CF_KEYCHAIN_SERVICE. Needs Pillow.
"""
import base64, io, json, lzma, os, re, statistics, subprocess, sys, time, unicodedata
import urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from PIL import Image

import medicare
import sbc

HERE = Path(__file__).resolve().parent
PRICE_PER_MTOK = 0.10            # Clairic input price; output is free
MAX_PIXELS = 1024 * 1024         # Clef caps images at 1,024 tokens of 32x32 px; assume Clairic is similar
MODEL = "@cf/cloudflare/clairic"


# ---------------------------------------------------------------- datasets
def num(s):
    """Plain decimal number, e.g. 'RM 193.00' -> 193.0."""
    m = re.search(r"-?\d[\d,]*\.?\d*", str(s))
    return float(m.group().replace(",", "")) if m else None


def idr(s):
    """Indonesian amount: '.' and ',' are thousands separators unless 1-2 digits follow the last one."""
    t = re.sub(r"[^\d.,\-]", "", str(s))
    if not re.search(r"\d", t):
        return None
    neg = t.startswith("-")
    t = t.lstrip("-")
    parts = re.split(r"[.,]", t)
    if len(parts) > 1 and len(parts[-1]) in (1, 2):
        v = float("".join(parts[:-1]) + "." + parts[-1])
    else:
        v = float("".join(parts))
    return -v if neg else v


DATE_FORMATS = ["%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y", "%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d",
                "%Y/%m/%d", "%d %b %Y", "%d-%b-%Y", "%d-%b-%y", "%d %B %Y", "%d%b%Y", "%b %d, %Y", "%d %b %y"]


def gt_date(s):
    s = re.sub(r"\s+", " ", str(s).strip().upper()).title()
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(s, f).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def first(x):
    return x[0] if isinstance(x, list) and x else x


def sroie_gt(p):
    return {"company": p.get("company"), "date": p.get("date"), "address": p.get("address"),
            "total": p.get("total")}


def cord_gt(p):
    tot, sub = first(p.get("total")) or {}, first(p.get("sub_total")) or {}
    menu = p.get("menu") or []
    menu = menu if isinstance(menu, list) else [menu]
    return {"total_price": tot.get("total_price"), "subtotal_price": sub.get("subtotal_price"),
            "tax_price": sub.get("tax_price"), "service_price": sub.get("service_price"),
            "cash_price": tot.get("cashprice"), "change_price": tot.get("changeprice"),
            "item_quantity": tot.get("menuqty_cnt"),
            "menu_items": [m["nm"] for m in menu if isinstance(m, dict) and m.get("nm")] or None}


def months(s):
    """Kleister term like '3 years' or '18 months' -> months."""
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*(year|month|week|day)", str(s), re.I)
    if not m:
        return None
    per = {"year": 12, "month": 1, "week": 12 / 52, "day": 12 / 365}[m.group(2).lower()]
    return float(m.group(1)) * per


def kleister(name, repo, list_keys=()):
    """Kleister dev-0 set: document text (the 'text_best' OCR column) plus the answer key.
    Answer-key values use '_' for spaces; keys that repeat (parties) become lists."""
    src = HERE / "data" / "_src" / repo
    if not src.exists():
        src.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", "--depth", "1", f"https://github.com/applicaai/{repo}", str(src)],
                       check=True, env={**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"})
    with lzma.open(src / "dev-0" / "in.tsv.xz", "rt", encoding="utf-8") as fh:
        rows = [l.split("\t") for l in fh.read().split("\n") if l.strip()]
    expected = (src / "dev-0" / "expected.tsv").read_text(encoding="utf-8").split("\n")
    d = HERE / "data" / name
    d.mkdir(parents=True, exist_ok=True)
    docs = []
    for idx, cols in enumerate(rows):
        text = next((c for c in reversed(cols[2:]) if c.strip()), "").replace("\\n", "\n")
        (d / f"{idx:04d}.txt").write_text(text, encoding="utf-8")
        gt = {}
        for tok in (expected[idx].split() if idx < len(expected) else []):
            k, _, v = tok.partition("=")
            v = v.replace("_", " ")
            if k in list_keys:
                gt.setdefault(k, []).append(v)
            else:
                gt[k] = v
        docs.append({"idx": idx, "text": f"{idx:04d}.txt", "gt": gt})
    return docs


def sbc_loader(name):
    """Both health-plan datasets come from one download; build them together and cache the other one too."""
    built = sbc.build(HERE / "data", shrink)
    for other, docs in built.items():
        if other != name:
            p = HERE / "data" / other / "gt.jsonl"
            p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in docs))
    return built[name]


def ma_loader(name):
    """Humana Medicare Advantage: both datasets come from one download, like the health-plan SBCs."""
    built = medicare.build(HERE / "data", shrink)
    for other, docs in built.items():
        if other != name:
            (HERE / "data" / other).mkdir(parents=True, exist_ok=True)
            (HERE / "data" / other / "gt.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in docs))
    return built[name]


IDR_NOTE = " Amounts are Indonesian rupiah: '.' and ',' separate thousands, so 60.000 means 60000."
DATASETS = {
    "sroie": {
        "hf": "podbilabs/sroie-donut", "split": "test", "gt": sroie_gt,
        "fields": {
            "company": {"type": "text", "instructions": "Name of the business that issued the receipt"},
            "date": {"type": "date", "instructions": "Date of the purchase. Receipts are from Malaysia, "
                     "so dates are written day first (DD/MM/YYYY)."},
            "address": {"type": "text", "instructions": "Full postal address of the business"},
            "total": {"type": "number", "decimals": 2, "unit": "MYR",
                      "instructions": "Grand total amount paid, including tax"},
        },
        "kinds": {"company": "text", "date": "date", "address": "text", "total": "number"},
    },
    "cord": {
        "hf": "naver-clova-ix/cord-v2", "split": "test", "gt": cord_gt,
        "fields": {
            "total_price": {"type": "number", "unit": "IDR",
                            "instructions": "Grand total to pay." + IDR_NOTE},
            "subtotal_price": {"type": "number", "unit": "IDR",
                               "instructions": "Subtotal before tax and service, if printed." + IDR_NOTE},
            "tax_price": {"type": "number", "unit": "IDR", "instructions": "Tax amount, if printed." + IDR_NOTE},
            "service_price": {"type": "number", "unit": "IDR",
                              "instructions": "Service charge amount, if printed." + IDR_NOTE},
            "cash_price": {"type": "number", "unit": "IDR",
                           "instructions": "Cash handed over by the customer, if printed." + IDR_NOTE},
            "change_price": {"type": "number", "unit": "IDR",
                             "instructions": "Change given back to the customer, if printed." + IDR_NOTE},
            "item_quantity": {"type": "number", "instructions": "Total quantity of items, if printed"},
            "menu_items": {"type": "list", "items": "text",
                           "instructions": "Name of each item ordered, as printed. Top-level items only, "
                                           "not add-ons or modifiers."},
        },
        "kinds": {"total_price": "idr", "subtotal_price": "idr", "tax_price": "idr", "service_price": "idr",
                  "cash_price": "idr", "change_price": "idr", "item_quantity": "number", "menu_items": "list"},
    },
    "nda": {
        "loader": lambda name: kleister(name, "kleister-nda", list_keys=("party",)),
        "fields": {
            "effective_date": {"type": "date", "instructions": "Date the agreement takes effect"},
            "jurisdiction": {"type": "text", "instructions": "US state or country whose law governs the agreement, "
                                                            "just its name (for example New York)"},
            "party": {"type": "list", "items": "text",
                      "instructions": "Name of each party (company or person) that signs the agreement"},
            "term": {"type": "duration", "unit": "months", "instructions": "Length of the agreement's term"},
        },
        "kinds": {"effective_date": "date", "jurisdiction": "text", "party": "list", "term": "months"},
    },
    "sbc_text": {"loader": sbc_loader, "fields": sbc.FIELDS, "kinds": sbc.KINDS},
    "sbc_image": {"loader": sbc_loader, "fields": sbc.FIELDS, "kinds": sbc.KINDS},
    "ma_text": {"loader": ma_loader, "fields": medicare.FIELDS, "kinds": medicare.KINDS},
    "ma_image": {"loader": ma_loader, "fields": medicare.FIELDS, "kinds": medicare.KINDS},
    "charity": {
        "loader": lambda name: kleister(name, "kleister-charity"),
        "fields": {
            "charity_name": {"type": "text", "instructions": "Registered name of the charity"},
            "charity_number": {"type": "text", "instructions": "Registered charity number"},
            "report_date": {"type": "date", "instructions": "End date of the financial period the report covers"},
            "income_annually_in_british_pounds": {
                "type": "number", "decimals": 2, "unit": "GBP",
                "instructions": "Total income for the year, in pounds (not thousands of pounds)"},
            "spending_annually_in_british_pounds": {
                "type": "number", "decimals": 2, "unit": "GBP",
                "instructions": "Total spending (expenditure) for the year, in pounds (not thousands of pounds)"},
            "address__postcode": {"type": "text", "instructions": "Postcode of the charity's principal address"},
            "address__post_town": {"type": "text", "instructions": "Post town of the charity's principal address"},
            "address__street_line": {"type": "text",
                                     "instructions": "Street line of the charity's principal address"},
        },
        "kinds": {"charity_name": "text", "charity_number": "text", "report_date": "date",
                  "income_annually_in_british_pounds": "gbp", "spending_annually_in_british_pounds": "gbp",
                  "address__postcode": "text", "address__post_town": "text", "address__street_line": "text"},
    },
}


# ---------------------------------------------------------------- download
def http_json(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.load(r)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 ** i)


def fetch(name):
    ds = DATASETS[name]
    d = HERE / "data" / name
    gt_path = d / "gt.jsonl"
    if gt_path.exists():
        return [json.loads(l) for l in gt_path.read_text().splitlines() if l]
    d.mkdir(parents=True, exist_ok=True)
    if "loader" in ds:
        docs = ds["loader"](name)
        gt_path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in docs))
        return docs
    info = http_json(f"https://datasets-server.huggingface.co/info?dataset={ds['hf']}")
    n = next(iter(info["dataset_info"].values()))["splits"][ds["split"]]["num_examples"]
    docs = []
    for off in range(0, n, 100):
        page = http_json(f"https://datasets-server.huggingface.co/rows?dataset={ds['hf']}&config=default"
                         f"&split={ds['split']}&offset={off}&length=100")
        for r in page["rows"]:
            idx, row = r["row_idx"], r["row"]
            raw = urllib.request.urlopen(row["image"]["src"], timeout=60).read()
            img_path = d / f"{idx:04d}.jpg"
            img_path.write_bytes(shrink(raw))
            parse = json.loads(row["ground_truth"])["gt_parse"]
            docs.append({"idx": idx, "image": img_path.name, "gt": ds["gt"](parse)})
        print(f"  downloaded {len(docs)}/{n}", flush=True)
    gt_path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in docs))
    return docs


def shrink(raw):
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    if im.width * im.height > MAX_PIXELS:
        s = (MAX_PIXELS / (im.width * im.height)) ** 0.5
        im = im.resize((int(im.width * s), int(im.height * s)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()


# ---------------------------------------------------------------- model call
def credentials():
    acct = os.environ.get("CLOUDFLARE_ACCOUNT_ID") or os.environ.get("CF_ACCOUNT_ID")
    tok = os.environ.get("CLOUDFLARE_API_TOKEN")
    service = os.environ.get("CF_KEYCHAIN_SERVICE")
    if not tok and sys.platform == "darwin" and service:
        r = subprocess.run(["security", "find-generic-password", "-a", os.environ.get("USER", ""),
                            "-s", service, "-w"], capture_output=True, text=True)
        tok = r.stdout.strip() or None
    if not acct or not tok:
        sys.exit("need CLOUDFLARE_ACCOUNT_ID (or CF_ACCOUNT_ID) and CLOUDFLARE_API_TOKEN")
    return acct, tok


def call(acct, tok, fields, img_bytes=None, text=None):
    payload = {"model": "clairic", "fields": fields}
    if text is not None:
        payload["state"] = text
    else:
        imgs = img_bytes if isinstance(img_bytes, list) else [img_bytes]
        payload["images"] = ["data:image/jpeg;base64," + base64.b64encode(b).decode() for b in imgs]
    body = json.dumps(payload).encode()
    url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{MODEL}"
    err = None
    for attempt in range(5):
        req = urllib.request.Request(url, data=body, headers={"Authorization": "Bearer " + tok,
                                                              "Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                j = json.load(r)
            return j.get("result", j), (time.time() - t0) * 1000, None
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}"
            if e.code not in (429, 500, 502, 503, 504):
                break
        except Exception as e:
            err = repr(e)
        time.sleep(1.5 * (attempt + 1))
    return None, None, err


def run(name, docs, workers):
    acct, tok = credentials()
    fields = DATASETS[name]["fields"]
    out_path = HERE / "results" / f"{name}.jsonl"
    out_path.parent.mkdir(exist_ok=True)
    done = {}
    if out_path.exists():
        for l in out_path.read_text().splitlines():
            r = json.loads(l)
            if r.get("response"):
                done[r["idx"]] = r
    todo = [d for d in docs if d["idx"] not in done]
    print(f"{name}: {len(done)} saved, {len(todo)} to run", flush=True)

    def one(d):
        if "text" in d:
            text = (HERE / "data" / name / d["text"]).read_text(encoding="utf-8")
            resp, ms, err = call(acct, tok, fields, text=text)
        elif "images" in d:
            resp, ms, err = call(acct, tok, fields, [(HERE / "data" / name / f).read_bytes() for f in d["images"]])
        else:
            resp, ms, err = call(acct, tok, fields, (HERE / "data" / name / d["image"]).read_bytes())
        return {"idx": d["idx"], "latency_ms": ms, "response": resp, "error": err}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, r in enumerate(ex.map(one, todo), 1):
            done[r["idx"]] = r
            if r["error"]:
                print(f"  {r['idx']}: {r['error']}", flush=True)
            if i % 25 == 0:
                print(f"  {i}/{len(todo)}", flush=True)
    out_path.write_text("\n".join(json.dumps(done[k], ensure_ascii=False) for k in sorted(done)))
    return done


# ---------------------------------------------------------------- scoring
def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).upper()
    return re.sub(r"[^A-Z0-9]", "", s)


def similarity(a, b):
    """1 - normalised edit distance on whitespace-collapsed uppercase text (ANLS-style)."""
    a, b = re.sub(r"\s+", " ", str(a).upper()).strip(), re.sub(r"\s+", " ", str(b).upper()).strip()
    if not a and not b:
        return 1.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return 1 - prev[-1] / max(len(a), len(b))


def judge(kind, gt, value):
    """Returns (correct, similarity) for a present ground truth and a non-null prediction."""
    if kind == "text":
        return norm(gt) == norm(value), similarity(gt, value)
    if kind == "date":
        g = gt_date(gt)
        ok = (g == str(value)[:10]) if g else norm(gt) == norm(value)
        return ok, float(ok)
    if kind == "months":
        g = months(gt)
        try:
            ok = g is not None and abs(float(value) - g) < 0.5
        except (TypeError, ValueError):
            ok = False
        return ok, float(ok)
    if kind == "gbp":   # Kleister's key mostly rounds to whole pounds, so match to within £1
        try:
            ok = abs(float(value) - num(gt)) < 1.0
        except (TypeError, ValueError):
            ok = False
        return ok, float(ok)
    if kind in ("number", "idr"):
        g = idr(gt) if kind == "idr" else num(gt)
        try:
            ok = g is not None and abs(float(value) - g) < 0.005
        except (TypeError, ValueError):
            ok = False
        return ok, float(ok)
    raise ValueError(kind)


def list_f1(gt, pred, fuzzy=False):
    """Item-level F1. Exact: same letters and digits. Fuzzy: similarity >= 0.8 counts as a match."""
    g, p = [str(x) for x in gt], [str(x) for x in (pred or [])]
    match = (lambda a, b: similarity(a, b) >= 0.8) if fuzzy else (lambda a, b: norm(a) == norm(b))
    pool, tp = list(g), 0
    for x in p:
        hit = next((y for y in pool if match(y, x)), None)
        if hit is not None:
            pool.remove(hit); tp += 1
    prec = tp / len(p) if p else 0.0
    rec = tp / len(g) if g else 0.0
    return 2 * prec * rec / (prec + rec) if prec + rec else 0.0


BANDS = [(0, 0.5), (0.5, 0.9), (0.9, 0.99), (0.99, 1.01)]


def score(name, docs, results, system="clairic"):
    kinds = DATASETS[name]["kinds"]
    per = {f: {"present": 0, "correct": 0, "wrong": 0, "missed": 0, "sim": [], "absent": 0,
               "correct_null": 0, "invented": 0, "f1": [], "f1_fuzzy": []} for f in kinds}
    answered = []        # (confidence, correct) for scalar answers where the truth is present
    lat, tokens, img_tokens, errors, examples = [], 0, [], 0, {f: [] for f in kinds}
    truncated = 0
    priced = 0.0         # baselines record their own per-call cost; Clairic's comes from tokens
    for d in docs:
        r = results.get(d["idx"])
        if not r or not r.get("response"):
            errors += 1
            continue
        resp = r["response"]
        lat.append(r["latency_ms"])
        u = resp.get("usage", {})
        tokens += u.get("input_tokens", 0)
        priced += r.get("cost_usd") or 0
        truncated += bool(resp.get("truncated"))
        if u.get("image_tokens"):
            img_tokens.append(u["image_tokens"])
        for f, kind in kinds.items():
            a = resp.get("answers", {}).get(f, {}) or {}
            gt, val, s = d["gt"].get(f), a.get("value"), per[f]
            if kind == "list":
                if gt:
                    s["present"] += 1
                    s["f1"].append(list_f1(gt, val))
                    s["f1_fuzzy"].append(list_f1(gt, val, fuzzy=True))
                continue
            if gt in (None, ""):
                s["absent"] += 1
                if val is None:
                    s["correct_null"] += 1
                else:
                    s["invented"] += 1
                    if len(examples[f]) < 3:
                        examples[f].append({"idx": d["idx"], "truth": None, "got": val})
                continue
            s["present"] += 1
            if val is None:
                s["missed"] += 1
                continue
            ok, sim = judge(kind, gt, val)
            s["correct" if ok else "wrong"] += 1
            s["sim"].append(sim)
            if "confidence" in a:
                answered.append((a["confidence"], ok))
            if not ok and len(examples[f]) < 3:
                examples[f].append({"idx": d["idx"], "truth": gt, "got": val,
                                    "confidence": round(a.get("confidence", -1), 3)})

    fields = {}
    for f, s in per.items():
        row = {"present": s["present"]}
        if kinds[f] == "list":
            row["mean_item_f1"] = round(statistics.mean(s["f1"]), 3) if s["f1"] else None
            row["mean_item_f1_fuzzy"] = round(statistics.mean(s["f1_fuzzy"]), 3) if s["f1_fuzzy"] else None
        else:
            row.update({
                "accuracy": round(s["correct"] / s["present"], 3) if s["present"] else None,
                "wrong": s["wrong"], "missed_as_null": s["missed"],
                "mean_similarity": round(statistics.mean(s["sim"] + [0.0] * s["missed"]), 3)
                if kinds[f] == "text" and s["present"] else None,
                "absent": s["absent"],
                "null_when_absent": round(s["correct_null"] / s["absent"], 3) if s["absent"] else None,
            })
        fields[f] = row
    calib = []
    for lo, hi in BANDS:
        b = [ok for c, ok in answered if lo <= c < hi]
        calib.append({"confidence": f"{lo:.2f}-{min(hi, 1):.2f}", "answers": len(b),
                      "share": round(len(b) / len(answered), 3) if answered else 0,
                      "accuracy": round(sum(b) / len(b), 3) if b else None})
    lat.sort()
    n_ok = len(lat)
    return {
        "system": system, "dataset": name, "images": len(docs), "scored": n_ok, "failed_calls": errors,
        "fields": fields, "calibration": calib,
        "overall_accuracy": round(sum(ok for _, ok in answered) / len(answered), 3) if answered else None,
        "latency_ms": {"median": round(lat[n_ok // 2]) if lat else None,
                       "p90": round(lat[int(n_ok * 0.9)]) if lat else None},
        "truncated_inputs": truncated, "input_tokens": tokens, "mean_image_tokens": round(statistics.mean(img_tokens)) if img_tokens else None,
        "cost_usd": round(priced or tokens / 1e6 * PRICE_PER_MTOK, 4),
        "examples_of_errors": examples,
    }


def show(rep):
    print(f"\n== {rep.get('system', 'clairic')} on {rep['dataset']}: {rep['scored']}/{rep['images']} images scored, {rep['failed_calls']} failed calls")
    print(f"   median {rep['latency_ms']['median']} ms, p90 {rep['latency_ms']['p90']} ms, "
          f"{rep['input_tokens']} input tokens (~{rep['mean_image_tokens']} per image), ${rep['cost_usd']}")
    print(f"   {'field':16} {'present':>7} {'accuracy':>8} {'wrong':>5} {'missed':>6} {'sim':>5} "
          f"{'absent':>6} {'null ok':>7}")
    for f, r in rep["fields"].items():
        if "mean_item_f1" in r:
            print(f"   {f:16} {r['present']:>7}   item F1 {r['mean_item_f1']} exact, "
                  f"{r['mean_item_f1_fuzzy']} near-match")
            continue
        fmt = lambda v: "-" if v is None else v
        print(f"   {f:16} {r['present']:>7} {fmt(r['accuracy']):>8} {r['wrong']:>5} {r['missed_as_null']:>6} "
              f"{fmt(r['mean_similarity']):>5} {r['absent']:>6} {fmt(r['null_when_absent']):>7}")
    print("   accuracy by confidence: " + "  ".join(
        f"{c['confidence']}: {c['accuracy']} ({c['answers']})" for c in rep["calibration"]))


def main():
    args = sys.argv[1:]
    if not args or args[0] not in DATASETS:
        sys.exit(__doc__)
    name = args[0]
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None
    workers = int(args[args.index("--workers") + 1]) if "--workers" in args else 6
    docs = fetch(name)
    if limit:
        docs = docs[:limit]
    if "--score" in args:
        p = HERE / "results" / f"{name}.jsonl"
        results = {json.loads(l)["idx"]: json.loads(l) for l in p.read_text().splitlines()} if p.exists() else {}
    else:
        results = run(name, docs, workers)
    rep = score(name, docs, results)
    show(rep)
    if not limit:
        (HERE / "reports").mkdir(exist_ok=True)
        (HERE / "reports" / f"{name}.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
