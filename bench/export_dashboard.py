#!/usr/bin/env python3
"""Turn bench/reports/*.json into the three CSV files the results dashboard reads (bench/dashboard/)."""
import csv, json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "dashboard"
SYSTEMS = {"clairic": "Clairic", "textract": "AWS Textract", "haiku-5-5": "Claude Haiku 5.5",
           "haiku-4-5": "Claude Haiku 4.5"}
DATASETS = {  # label, short note under the label, what the documents are, order on the page
    "sroie": ("SROIE", "Receipt scans", "Scanned receipts, image", 0),
    "cord": ("CORD", "Receipt photos", "Receipt photos, image", 1),
    "sbc_text": ("Health plans (text)", "ACA plan summaries", "Summary of Benefits and Coverage, text", 2),
    "sbc_image": ("Health plans (images)", "ACA plan summaries", "Summary of Benefits and Coverage, page images", 3),
    "ma_text": ("Medicare plans (text)", "Humana 2027", "Humana Medicare Advantage Summary of Benefits, text", 4),
    "ma_image": ("Medicare plans (images)", "Humana 2027", "Humana Medicare Advantage Summary of Benefits, page images", 5),
    "nda": ("Kleister NDA", "Legal agreements", "Non-disclosure agreements, text", 6),
    "charity": ("Kleister Charity", "Charity reports", "Charity annual reports, text", 7),
}
FIELDS = {
    "company": "Company name", "date": "Date", "address": "Address", "total": "Total",
    "total_price": "Total", "subtotal_price": "Subtotal", "tax_price": "Tax", "service_price": "Service charge",
    "cash_price": "Cash paid", "change_price": "Change", "item_quantity": "Item count",
    "menu_items": "Item names",
    "effective_date": "Effective date", "jurisdiction": "Jurisdiction", "party": "Parties", "term": "Term",
    "charity_name": "Charity name", "charity_number": "Charity number", "report_date": "Report date",
    "income_annually_in_british_pounds": "Income (£)", "spending_annually_in_british_pounds": "Spending (£)",
    "address__postcode": "Postcode", "address__post_town": "Town", "address__street_line": "Street",
    "deductible_individual": "Deductible, individual", "deductible_family": "Deductible, family",
    "oop_max_individual": "Out-of-pocket limit, individual", "oop_max_family": "Out-of-pocket limit, family",
    "pcp_copay": "Primary care copay", "specialist_copay": "Specialist copay", "urgent_care_copay": "Urgent care copay",
    "er_copay": "ER copay", "er_coinsurance_pct": "ER coinsurance %", "generic_rx_copay": "Generic drug copay",
    "peg_total": "Example: having a baby", "joe_total": "Example: diabetes", "mia_total": "Example: fracture",
    "moop_in_network": "Out-of-pocket max", "medical_deductible": "Medical deductible",
    "inpatient_copay_per_day": "Hospital copay per day", "inpatient_copay_days": "Hospital copay days",
    "drug_deductible": "Drug deductible", "generic_copay": "Generic drug copay",
}


def pct(x):
    return "" if x is None else round(100 * x, 1)


def main():
    runs, fields, calib = [], [], []
    for path in sorted((HERE / "reports").glob("*.json")):
        rep = json.loads(path.read_text())
        system, ds = rep.get("system", "clairic"), rep["dataset"]
        label, note, about, order = DATASETS[ds]
        base = {"system": system, "system_label": SYSTEMS.get(system, system), "dataset": ds,
                "dataset_label": label, "dataset_note": note, "dataset_about": about, "dataset_order": order}
        scalar = [r["accuracy"] for r in rep["fields"].values() if r.get("accuracy") is not None]
        nulls = [(r["absent"], r["null_when_absent"]) for r in rep["fields"].values()
                 if r.get("absent") and r.get("null_when_absent") is not None]
        hi = [c for c in rep["calibration"] if c["confidence"] in ("0.90-0.99", "0.99-1.00")]
        hi_n = sum(c["answers"] for c in hi)
        all_n = sum(c["answers"] for c in rep["calibration"])
        runs.append({**base, "images": rep["images"], "failed_calls": rep["failed_calls"],
                     "mean_field_accuracy": pct(sum(scalar) / len(scalar)) if scalar else "",
                     "null_ok": pct(sum(n * v for n, v in nulls) / sum(n for n, _ in nulls)) if nulls else "",
                     "confident_accuracy": pct(sum(c["answers"] * (c["accuracy"] or 0) for c in hi) / hi_n) if hi_n else "",
                     "confident_share": pct(hi_n / all_n) if all_n else "",
                     "median_ms": rep["latency_ms"]["median"], "p90_ms": rep["latency_ms"]["p90"],
                     "cost_usd": rep["cost_usd"],
                     "cost_per_1k": round(rep["cost_usd"] / max(rep["scored"], 1) * 1000, 2),
                     "truncated": rep.get("truncated_inputs", 0)})
        for i, (f, r) in enumerate(rep["fields"].items()):
            is_list = "mean_item_f1_fuzzy" in r
            fields.append({**base, "field": f, "field_label": FIELDS.get(f, f), "field_order": i,
                           "kind": "list" if is_list else "value", "present": r["present"],
                           "accuracy": pct(r["mean_item_f1_fuzzy"] if is_list else r["accuracy"]),
                           "absent": r.get("absent", ""), "null_ok": pct(r.get("null_when_absent"))})
        for i, c in enumerate(rep["calibration"]):
            calib.append({**base, "band": c["confidence"], "band_order": i, "answers": c["answers"],
                          "share": pct(c["share"]), "accuracy": pct(c["accuracy"])})
    OUT.mkdir(exist_ok=True)
    for name, rows in (("runs", runs), ("fields", fields), ("calibration", calib)):
        with open(OUT / f"{name}.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {name}.csv ({len(rows)} rows)")


if __name__ == "__main__":
    main()
