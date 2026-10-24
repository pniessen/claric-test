"""Health-plan test set: ACA Marketplace plans' Summary of Benefits and Coverage (SBC) documents, scored
against CMS's 2026 Marketplace public use files (the same plans' benefit values as structured data).

Builds two datasets from the same plans: `sbc_text` (the SBC as layout-preserving text) and `sbc_image`
(pages 1-3 plus the coverage-examples page, as images). SBCs belong to the insurers: test with them,
don't redistribute them.
"""
import csv, io, random, re, subprocess, urllib.request, zipfile
from collections import defaultdict
from pathlib import Path

PUF = "https://download.cms.gov/marketplace-puf/2026/{}.zip"
SAMPLE, PER_ISSUER, SEED = 150, 2, 2026
SKIP_HOSTS = ("medica.com", "widen.net")   # bot-blocked or viewer pages, per a spot check of 178 links
BENEFITS = {
    "Primary Care Visit to Treat an Injury or Illness": "pcp",
    "Specialist Visit": "specialist",
    "Urgent Care Centers or Facilities": "urgent_care",
    "Emergency Room Services": "er",
    "Generic Drugs": "generic_rx",
}
USD = {"type": "number", "decimals": 2, "unit": "USD"}
FIELDS = {
    "deductible_individual": {**USD, "instructions": "In-network overall deductible for one person. If drugs "
                              "have a separate deductible, the medical deductible. 0 if there is none."},
    "deductible_family": {**USD, "instructions": "In-network overall deductible for a family (whole family). "
                          "If drugs have a separate deductible, the medical deductible. 0 if there is none."},
    "oop_max_individual": {**USD, "instructions": "In-network out-of-pocket limit for one person"},
    "oop_max_family": {**USD, "instructions": "In-network out-of-pocket limit for a family (whole family)"},
    "pcp_copay": {**USD, "instructions": "In-network copay per primary care visit to treat an injury or "
                  "illness. 0 if no charge. Empty if coinsurance applies instead of a copay."},
    "specialist_copay": {**USD, "instructions": "In-network copay per specialist visit. 0 if no charge. "
                         "Empty if coinsurance applies instead of a copay."},
    "urgent_care_copay": {**USD, "instructions": "In-network copay for urgent care. 0 if no charge. "
                          "Empty if coinsurance applies instead of a copay."},
    "er_copay": {**USD, "instructions": "In-network copay for emergency room care (facility fee). 0 if no "
                 "charge. Empty if coinsurance applies instead of a copay."},
    "er_coinsurance_pct": {"type": "number", "unit": "percent", "instructions": "In-network coinsurance "
                           "percentage for emergency room care (facility fee). Empty if only a copay applies."},
    "generic_rx_copay": {**USD, "instructions": "In-network retail copay per prescription for generic "
                         "(tier 1) drugs. 0 if no charge. Empty if coinsurance applies instead of a copay."},
    "peg_total": {**USD, "instructions": "In the 'Peg is Having a Baby' coverage example, the total Peg "
                  "would pay"},
    "joe_total": {**USD, "instructions": "In the 'Managing Joe's Type 2 Diabetes' coverage example, the "
                  "total Joe would pay"},
    "mia_total": {**USD, "instructions": "In the 'Mia's Simple Fracture' coverage example, the total Mia "
                  "would pay"},
}
KINDS = {f: "number" for f in FIELDS}


# ---------------------------------------------------------------- answer key from the public use files
def money(s):
    """'$6,000 ' -> 6000; '$12000 per group' -> 12000; 'No Charge...' -> 0; blank/'Not Applicable' -> None."""
    s = (s or "").strip()
    if not s or s.lower().startswith("not applicable"):
        return None
    if s.lower().startswith("no charge"):
        return 0.0
    m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", s)
    return float(m.group(1).replace(",", "")) if m else None


def pct(s):
    m = re.match(r"\s*([\d.]+)\s*%", s or "")
    return float(m.group(1)) if m else None


def puf_rows(src, name, keep=lambda row: True):
    zpath = src / f"{name}.zip"
    if not zpath.exists():
        zpath.write_bytes(urllib.request.urlopen(PUF.format(name), timeout=300).read())
    with zipfile.ZipFile(zpath) as z:
        member = next(n for n in z.namelist() if n.endswith(".csv"))
        with z.open(member) as fh:
            for row in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig", newline="")):
                if keep(row):
                    yield row


def first_set(row, *cols):
    for c in cols:
        if (row.get(c) or "").strip():
            return row[c]
    return ""


def answer_key(plan, benefits):
    gt = {
        "deductible_individual": money(first_set(plan, "TEHBDedInnTier1Individual", "MEHBDedInnTier1Individual")),
        "deductible_family": money(first_set(plan, "TEHBDedInnTier1FamilyPerGroup", "MEHBDedInnTier1FamilyPerGroup")),
        "oop_max_individual": money(first_set(plan, "TEHBInnTier1IndividualMOOP", "MEHBInnTier1IndividualMOOP")),
        "oop_max_family": money(first_set(plan, "TEHBInnTier1FamilyPerGroupMOOP", "MEHBInnTier1FamilyPerGroupMOOP")),
    }
    for benefit, key in BENEFITS.items():
        b = benefits.get(benefit)
        covered = b and b.get("IsCovered", "").strip().lower() == "covered"
        gt[f"{key}_copay"] = money(b["CopayInnTier1"]) if covered else None
        if key == "er":
            p = pct(b["CoinsInnTier1"]) if covered else None
            gt["er_coinsurance_pct"] = p if p else None   # 0% alongside a copay isn't stated in the SBC
    for person, prefix in (("peg", "SBCHavingaBaby"), ("joe", "SBCHavingDiabetes"), ("mia", "SBCHavingSimplefracture")):
        parts = [money(plan.get(f"{prefix}{p}")) for p in ("Deductible", "Copayment", "Coinsurance", "Limit")]
        gt[f"{person}_total"] = sum(parts) if all(p is not None for p in parts) else None
    return {k: (None if v is None else f"{v:.2f}") for k, v in gt.items()}


# ---------------------------------------------------------------- documents
def fetch_pdf(url, dest):
    url = re.sub(r"\s+", "", url)
    if any(h in url.lower() for h in SKIP_HOSTS):
        return False
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "clairic-bench/0.1 (research benchmark)"})
        data = urllib.request.urlopen(req, timeout=60).read()
    except Exception:
        return False
    if not data.startswith(b"%PDF"):
        return False
    dest.write_bytes(data)
    return True


def page_text(pdf, page):
    return subprocess.run(["pdftotext", "-layout", "-f", str(page), "-l", str(page), str(pdf), "-"],
                          capture_output=True, text=True).stdout


def layout_text(pdf):
    text = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True).stdout
    return re.sub(r"[ \t]{3,}", " | ", text)   # keep table columns apart without long runs of spaces


def page_images(pdf, out_dir, stem, shrink):
    pages = int(re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", str(pdf)], capture_output=True,
                                                            text=True).stdout).group(1))
    examples = next((p for p in range(pages, 0, -1) if "having a baby" in page_text(pdf, p).lower()), None)
    chosen = [p for p in (1, 2, 3) if p <= pages] + ([examples] if examples and examples > 3 else [])
    names = []
    for p in chosen:
        base = out_dir / f"{stem}-p{p}"
        subprocess.run(["pdftoppm", "-f", str(p), "-l", str(p), "-r", "110", "-png", "-singlefile", str(pdf),
                        str(base)], check=True)
        png = base.with_suffix(".png")
        jpg = out_dir / f"{stem}-p{p}.jpg"
        jpg.write_bytes(shrink(png.read_bytes()))
        png.unlink()
        names.append(jpg.name)
    return names


def build(data_dir: Path, shrink):
    """Pick plans, download their SBCs and write both datasets' files. Returns {name: docs}."""
    src = data_dir / "_src" / "puf2026"
    (src / "sbc").mkdir(parents=True, exist_ok=True)
    plans = [r for r in puf_rows(src, "plan-attributes-puf")
             if r["DentalOnlyPlan"] == "No" and r["PlanId"].endswith("-01")
             and r["URLForSummaryofBenefitsCoverage"].strip()]
    by_issuer = defaultdict(list)
    for r in plans:
        by_issuer[r["IssuerId"]].append(r)
    rng = random.Random(SEED)
    candidates = []
    for issuer in sorted(by_issuer):
        group = sorted(by_issuer[issuer], key=lambda r: r["PlanId"])
        rng.shuffle(group)
        candidates.extend(group[: PER_ISSUER + 2])   # spares in case a link is dead
    rng.shuffle(candidates)

    chosen, per = [], defaultdict(int)
    for r in candidates:
        if len(chosen) >= SAMPLE or per[r["IssuerId"]] >= PER_ISSUER:
            continue
        pdf = src / "sbc" / f"{r['PlanId']}.pdf"
        if pdf.exists() or fetch_pdf(r["URLForSummaryofBenefitsCoverage"], pdf):
            chosen.append(r)
            per[r["IssuerId"]] += 1
            if len(chosen) % 25 == 0:
                print(f"  {len(chosen)} SBCs downloaded", flush=True)

    ids = {r["PlanId"] for r in chosen}
    benefits = defaultdict(dict)
    for b in puf_rows(src, "benefits-and-cost-sharing-puf",
                      keep=lambda row: row["PlanId"] in ids and row["BenefitName"] in BENEFITS):
        benefits[b["PlanId"]][b["BenefitName"]] = b

    text_dir, img_dir = data_dir / "sbc_text", data_dir / "sbc_image"
    text_dir.mkdir(exist_ok=True)
    img_dir.mkdir(exist_ok=True)
    text_docs, img_docs = [], []
    for idx, r in enumerate(sorted(chosen, key=lambda r: r["PlanId"])):
        pdf = src / "sbc" / f"{r['PlanId']}.pdf"
        gt = answer_key(r, benefits[r["PlanId"]])
        meta = {"plan_id": r["PlanId"], "issuer": r["IssuerMarketPlaceMarketingName"], "state": r["StateCode"],
                "metal": r["MetalLevel"]}
        (text_dir / f"{idx:04d}.txt").write_text(layout_text(pdf), encoding="utf-8")
        text_docs.append({"idx": idx, "text": f"{idx:04d}.txt", "gt": gt, "meta": meta})
        img_docs.append({"idx": idx, "images": page_images(pdf, img_dir, f"{idx:04d}", shrink), "gt": gt,
                         "meta": meta})
    return {"sbc_text": text_docs, "sbc_image": img_docs}
