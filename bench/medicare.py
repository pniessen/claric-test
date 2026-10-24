"""Medicare Advantage test set: Humana's 2027 Summary of Benefits (SB) documents, scored against CMS's
2027 Plan Benefit Package (PBP) data for the same plans.

Builds `ma_text` (the SB as layout-preserving text) and `ma_image` (the benefits-table pages and the
drug-tier page as images). Only individual, non-special-needs HMO and PPO plans with unambiguous values
are used. SBs belong to Humana: test with them, don't redistribute them.
"""
import csv, io, random, re, subprocess, time, urllib.request, zipfile
from collections import defaultdict
from pathlib import Path

PBP_ZIP = "https://www.cms.gov/files/zip/pbp-benefits-2027.zip"
SB_URL = "https://assets.humana.com/is/content/humana/{h}{plan}{seg}SB27pdf"
SAMPLE, PER_CONTRACT, SEED = 100, 5, 2027
PLAN_TYPES = {"01": "HMO", "02": "HMO-POS", "04": "Local PPO", "31": "Regional PPO"}
USD = {"type": "number", "decimals": 2, "unit": "USD"}
FIELDS = {
    "moop_in_network": {**USD, "instructions": "Medical maximum out-of-pocket amount for in-network services"},
    "medical_deductible": {**USD, "instructions": "In-network medical (not pharmacy) deductible. 0 if the plan "
                           "has none."},
    "pcp_copay": {**USD, "instructions": "In-network copay for a primary care provider visit. 0 if $0."},
    "specialist_copay": {**USD, "instructions": "In-network copay for a specialist visit"},
    "er_copay": {**USD, "instructions": "Copay for an emergency room visit"},
    "urgent_care_copay": {**USD, "instructions": "Copay for an urgent care visit"},
    "inpatient_copay_per_day": {**USD, "instructions": "In-network inpatient hospital copay per day for the "
                                "first days of a stay (the first per-day amount listed)"},
    "inpatient_copay_days": {"type": "number", "integer": True, "instructions": "The last day number that the "
                             "first per-day inpatient hospital copay applies to (e.g. 5 for 'days 1-5')"},
    "drug_deductible": {**USD, "instructions": "Pharmacy (Part D) deductible: the highest deductible amount "
                        "that applies to any drug tier. 0 if none. Empty if the plan has no drug coverage."},
    "generic_copay": {**USD, "instructions": "Tier 1 (Preferred Generic) drug copay under retail cost-sharing "
                      "for a 30-day supply, in the first drug-cost table (before any coverage changes). "
                      "Empty if the plan has no drug coverage."},
}
KINDS = {f: "number" for f in FIELDS}


# ---------------------------------------------------------------- answer key from PBP
def pbp_tables(src, names):
    zpath = src / "pbp-benefits-2027.zip"
    if not zpath.exists():
        zpath.write_bytes(urllib.request.urlopen(PBP_ZIP, timeout=600).read())
    out = {}
    with zipfile.ZipFile(zpath) as z:
        members = {Path(n).stem.lower(): n for n in z.namelist() if n.lower().endswith(".txt")}
        for name in names:
            with z.open(members[name.lower()]) as fh:
                rows = csv.DictReader(io.TextIOWrapper(fh, encoding="latin-1", newline=""), delimiter="\t")
                table = {}
                for r in rows:
                    r = {k.lower(): (v or "").strip() for k, v in r.items() if k}
                    key = f"{r['pbp_a_hnumber']}-{r['pbp_a_plan_identifier'].zfill(3)}-{r['segment_id'].zfill(3)}"
                    if name == "pbp_mrx_tier":
                        if r.get("mrx_tier_id") == "1":
                            table.setdefault(key, r)
                    else:
                        table.setdefault(key, r)
                out[name] = table
    return out


def amount(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def single(r, prefix):
    """A copay stored as min/max: usable only when both are the same single amount."""
    lo, hi = amount(r.get(f"{prefix}_min")), amount(r.get(f"{prefix}_max"))
    return lo if lo is not None and lo == hi else None


def answer_key(a, d, b7, b4, b1, mrx, tier1):
    """Returns the answer key, or None if any field is ambiguous for this plan."""
    if d.get("pbp_d_ann_deduct_amt_type") in ("1", "2", "3"):   # Medicare-defined deductible: no dollar figure
        return None
    if b7.get("pbp_b7d_coins_yn") == "1":
        return None
    gt = {
        "moop_in_network": amount(d.get("pbp_d_out_pocket_amt")),
        # In-network deductible if the plan has one, else the plan-wide deductible, else none
        "medical_deductible": (amount(d.get("pbp_d_inn_deduct_amt")) if d.get("pbp_d_inn_deduct_yn") == "1" else
                               amount(d.get("pbp_d_ann_deduct_amt")) if d.get("pbp_d_ann_deduct_yn") == "1" else 0.0),
        "pcp_copay": single(b7, "pbp_b7a_copay_amt_mc"),
        "specialist_copay": single(b7, "pbp_b7d_copay_amt_mc"),
        "er_copay": single(b4, "pbp_b4a_copay_amt_mc"),
        "urgent_care_copay": single(b4, "pbp_b4b_copay_amt_mc"),
        "inpatient_copay_per_day": amount(b1.get("pbp_b1a_copay_mcs_amt_int1_t1")),
        "inpatient_copay_days": amount(b1.get("pbp_b1a_copay_mcs_endd_int1_t1")),
    }
    if any(v is None for v in gt.values()):
        return None
    has_drugs = mrx.get("mrx_drug_ben_yn") == "1"
    if has_drugs:
        charge = mrx.get("mrx_alt_ded_charge")
        gt["drug_deductible"] = 0.0 if charge == "3" else amount(mrx.get("mrx_alt_ded_amount"))
        gt["generic_copay"] = amount(tier1.get("mrx_tier_rstd_copay_1m"))
        if gt["drug_deductible"] is None or gt["generic_copay"] is None:
            return None
    else:
        gt["drug_deductible"] = gt["generic_copay"] = None
    return {k: (None if v is None else f"{v:.2f}") for k, v in gt.items()}


# ---------------------------------------------------------------- documents
def fetch_sb(plan_id, dest):
    h, plan, seg = plan_id.split("-")
    req = urllib.request.Request(SB_URL.format(h=h, plan=plan, seg=seg),
                                 headers={"User-Agent": "clairic-bench/0.1 (research benchmark)"})
    try:
        data = urllib.request.urlopen(req, timeout=120).read()
    except Exception:
        return False
    if not data.startswith(b"%PDF"):
        return False
    dest.write_bytes(data)
    return True


def pdf_pages(pdf):
    n = int(re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", str(pdf)], capture_output=True,
                                                        text=True).stdout).group(1))
    return [subprocess.run(["pdftotext", "-layout", "-f", str(p), "-l", str(p), str(pdf), "-"],
                           capture_output=True, text=True).stdout for p in range(1, n + 1)]


def key_pages(pages):
    """1-based numbers of the benefits-table pages, the ER/urgent-care page and the drug-tier page (at most 4)."""
    core = next((i + 1 for i, t in enumerate(pages) if "Medical deductible" in t or
                 "Medical Maximum out-of-pocket" in t), 4)
    chosen = [core, core + 1]
    er = next((i + 1 for i, t in enumerate(pages) if i + 1 >= core and re.search(r"Emergency (room|care)", t)
               and re.search(r"Urgent", t)), None)
    if er and er not in chosen:
        chosen.append(er)
    drug = next((i + 1 for i, t in enumerate(pages) if i + 1 > core + 1 and "Tier 1" in t and
                 re.search(r"retail cost-sharing", t, re.I)), None)
    if drug:
        chosen.append(drug)
    return [p for p in chosen if p <= len(pages)]


def build(data_dir: Path, shrink, reuse_dir: Path | None = None):
    src = data_dir / "_src" / "pbp2027"
    (src / "sb").mkdir(parents=True, exist_ok=True)
    t = pbp_tables(src, ["pbp_Section_A", "pbp_Section_D", "pbp_b7_health_prof", "pbp_b4_emerg_urgent",
                         "pbp_b1a_inpat_hosp", "pbp_mrx", "pbp_mrx_tier"])
    A = t["pbp_Section_A"]
    cand = []
    for pid, a in A.items():
        humana = "humana" in (a.get("pbp_a_org_marketing_name", "") + a.get("pbp_a_org_name", "")).lower()
        if not humana or a.get("pbp_a_eghp_yn") != "2" or a.get("pbp_a_special_need_plan_type"):
            continue
        ptype = PLAN_TYPES.get(a.get("pbp_a_plan_type", "").zfill(2))
        if not ptype:
            continue
        gt = answer_key(a, t["pbp_Section_D"].get(pid, {}), t["pbp_b7_health_prof"].get(pid, {}),
                        t["pbp_b4_emerg_urgent"].get(pid, {}), t["pbp_b1a_inpat_hosp"].get(pid, {}),
                        t["pbp_mrx"].get(pid, {}), t["pbp_mrx_tier"].get(pid, {}))
        if gt:
            cand.append((pid, ptype, a.get("pbp_a_plan_name", ""), gt))
    print(f"  {len(cand)} eligible Humana plans", flush=True)
    rng = random.Random(SEED)
    rng.shuffle(cand)
    chosen, per = [], defaultdict(int)
    for pid, ptype, name, gt in cand:
        if len(chosen) >= SAMPLE or per[pid[:5]] >= PER_CONTRACT:
            continue
        pdf = src / "sb" / f"{pid}.pdf"
        if not pdf.exists() and reuse_dir and (reuse_dir / f"{pid}.pdf").exists():
            pdf.write_bytes((reuse_dir / f"{pid}.pdf").read_bytes())
        if not pdf.exists():
            ok = fetch_sb(pid, pdf)
            time.sleep(1.0)   # one request at a time, with a pause
            if not ok:
                continue
        chosen.append((pid, ptype, name, gt))
        per[pid[:5]] += 1
        if len(chosen) % 20 == 0:
            print(f"  {len(chosen)} SBs ready", flush=True)

    text_dir, img_dir = data_dir / "ma_text", data_dir / "ma_image"
    text_dir.mkdir(exist_ok=True)
    img_dir.mkdir(exist_ok=True)
    text_docs, img_docs = [], []
    for idx, (pid, ptype, name, gt) in enumerate(sorted(chosen)):
        pdf = src / "sb" / f"{pid}.pdf"
        pages = pdf_pages(pdf)
        (text_dir / f"{idx:04d}.txt").write_text(re.sub(r"[ \t]{3,}", " | ", "\n".join(pages)), encoding="utf-8")
        names = []
        for p in key_pages(pages):
            base = img_dir / f"{idx:04d}-p{p}"
            subprocess.run(["pdftoppm", "-f", str(p), "-l", str(p), "-r", "110", "-png", "-singlefile", str(pdf),
                            str(base)], check=True)
            png = base.with_suffix(".png")
            (img_dir / f"{idx:04d}-p{p}.jpg").write_bytes(shrink(png.read_bytes()))
            png.unlink()
            names.append(f"{idx:04d}-p{p}.jpg")
        meta = {"plan_id": pid, "plan_type": ptype, "plan_name": name}
        text_docs.append({"idx": idx, "text": f"{idx:04d}.txt", "gt": gt, "meta": meta})
        img_docs.append({"idx": idx, "images": names, "gt": gt, "meta": meta})
    return {"ma_text": text_docs, "ma_image": img_docs}
