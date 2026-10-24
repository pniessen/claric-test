# Clairic on public receipt datasets

`clairic_bench.py` runs Clairic on two public receipt datasets with answer keys, from images alone (no OCR text given):

- **SROIE**: 347 scanned Malaysian receipts, with company, date, address and total.
- **CORD v2**: 100 photographed Indonesian receipts, with total, subtotal, tax, service charge, cash, change, item count and line items. Fields a receipt doesn't print are blank in the answer key, so these receipts also test whether Clairic returns empty values.

```sh
python3 bench/clairic_bench.py sroie          # or: cord   (add --limit 10 for a quick check)
python3 bench/clairic_bench.py cord --score   # re-score saved answers without calling the model
```

Images are shrunk to at most 1 megapixel. Running them at full resolution made no difference; see the notes below. Full summaries are in [`reports/`](reports/).

## Results (2026-10-24)

| | SROIE | CORD |
|---|---|---|
| Images | 347, none failed | 100, none failed |
| Median / p90 latency | 678 / 1,096 ms | 526 / 899 ms |
| Cost for the whole set | $0.118 | $0.018 |

**Numbers and dates are strong. Long text read from images is not.**

| Field | Accuracy |
|---|---|
| SROIE total | 93.1% |
| SROIE date | 94.2% |
| CORD total | 96.8% |
| CORD change | 96.4% |
| CORD subtotal | 90.8% |
| CORD tax | 88.4% |
| CORD cash | 88.1% |
| CORD service charge | 83.3% |
| SROIE company name | 2.3% exact (mean similarity 0.60) |
| SROIE address | 0% exact (mean similarity 0.39) |
| CORD line-item names | item F1 0.11 exact, 0.21 near-match |

**Missing fields come back empty.** When CORD's answer key has no value, Clairic returned an empty value for:
- tax: 98%
- service charge: 99%
- cash: 100%
- change: 98%
- subtotal: 91%

**Confidence tracks accuracy.**

| Confidence | SROIE accuracy | CORD accuracy |
|---|---|---|
| below 0.5 | 6% | 77% |
| 0.5 – 0.9 | 28% | 93% |
| 0.9 – 0.99 | 93% | 99% |
| 0.99 and above | 98% | 100% |

Keeping only SROIE answers with confidence of at least 0.9 gives 96% accuracy on 38% of fields.

## Notes

- **Garbled long text.** Names and addresses come back garbled, with repeated fragments, e.g. `NO 2 & 4, JalanAYUU,, BANDARIARI,,11,11117,,AI`. On 20 receipts, full-resolution images (twice the image tokens) and header-only crops scored the same as the 1-megapixel images. So the cause is the model, not the image size. Clairic does flag these answers with low confidence, mostly 0.3–0.5.
- **Answer-key gaps.** CORD has no `total_price` for 5 receipts that don't print a "Total" line. Clairic returned the amount payable, which is arguably right, so the "null ok 0.0" figure for that field is an answer-key artefact.
- **Item counts.** Clairic often counts the items itself when no count is printed, despite the "if printed" instruction.
- **Comparing with published scores.** CC-OCR (arXiv 2412.02210) reports field-level F1 for zero-shot vision LLMs on the same 347 SROIE images. GPT-4o scores 72.6 and Claude 3.5 Sonnet 69.8. Clairic averages 47% over the four SROIE fields here, but text normalisation differs, so treat that as a rough comparison only. Our CORD numbers cover a subset of fields and can't be compared with published CORD scores.
- **Scoring rules.**
  - Text: exact match after keeping only letters and digits, ignoring case.
  - Dates: compared as calendar dates. SROIE's answer key is day-first, and the field instructions say so.
  - Amounts: must match within 0.005, with CORD's `60.000` read as 60000.

## More test sets

- **Kleister NDA** (`nda`): 83 SEC non-disclosure agreements as text. Fields: effective date, governing-law jurisdiction, parties (list) and term (duration, scored in months). The answer keys come from the Kleister dev sets on GitHub (applicaai).
- **Kleister Charity** (`charity`): 440 UK charity annual reports as text. Fields: income and spending in £, report date, charity number and name, postcode, town and street.
- **Health-plan SBCs** (`sbc_text`, `sbc_image`): 150 ACA Marketplace plans (74 insurers, 29 states), at most two per insurer.
  - **Documents:** each plan's Summary of Benefits and Coverage, sent either as layout-preserving text or as page images (pages 1–3 plus the coverage-examples page).
  - **Answer key:** CMS's 2026 Marketplace public use files.
  - **Fields:** in-network deductible and out-of-pocket limit (individual and family); copays for primary care, specialist, urgent care, ER and generic drugs; ER coinsurance; and the totals in the three coverage examples.
  - **Empty answers:** a copay is blank in the answer key when coinsurance applies instead, so these cases also test whether Clairic leaves the field empty.
  - **Downloading:** `sbc.py` downloads the files itself. The SBCs belong to the insurers, so they are used only for testing and not committed.

## Clairic vs Claude Haiku (2026-10-24)

Average field accuracy over each test set's single-value fields. Cost is per 1,000 documents. Every run completed with no failed calls.

| Test set | Clairic | Haiku 5.5 | Haiku 4.5 | Clairic $/1k | Haiku 5.5 $/1k | Haiku 4.5 $/1k |
|---|---|---|---|---|---|---|
| SROIE receipts (image) | 47.4% | **89.0%** | 79.5% | 0.34 | 0.30 | 2.57 |
| CORD receipts (image) | 91.5% | **95.9%** | 84.2% | 0.18 | 0.43 | 3.52 |
| Health plans (text) | 86.0% | **98.4%** | 97.5% | 0.74 | 1.41 | 10.87 |
| Health plans (images) | 93.8% | **95.1%** | 94.6% | 0.54 | 1.00 | 9.27 |
| Kleister NDA (text) | 88.5% | **94.5%** | 93.8% | 0.73 | 0.77 | 4.94 |
| Kleister Charity (text) | **82.8%** | 80.6% | 79.7% | 3.15 | 2.04 | 14.37 |

- **Totals:** Clairic cost $1.77, Haiku 5.5 $1.47 and Haiku 4.5 $11.00.
- **Speed:** Clairic is fastest on text, with a median of 0.6–0.7 s per document against about 2 s for Haiku 5.5. On page images, all three take 2–3 s.
- **Where Clairic falls short:** reading names and lists. It got 2% of SROIE company names exactly right (Haiku 5.5: 76%), 17% F1 on NDA parties (90%) and 21% F1 on CORD item names (91%).
- **Empty answers:** when a health plan charges coinsurance instead of a copay, Clairic usually returns $0 where it should leave the copay empty. It left only 21% of these empty on text and 46% on images; Haiku 5.5 left 83% and 89%.
- **Where Clairic is close:** amounts, dates and coverage-example totals. It also had the best average on the long Charity reports.
- **Confidence:** all three systems' answers rated 0.9 or above were 92–100% right, except Haiku 4.5 on Charity (83%). Haiku's figure is self-reported.

## Medicare Advantage: Humana 2027 (`ma_text`, `ma_image`)

- **Plans:** 96 Humana individual HMO and PPO plans from 30 contracts. Special-needs plans are excluded, because their documents often say things like "$0 or 20% depending on Medicaid level".
- **Documents:** each plan's Summary of Benefits, sent either as text or as up to 4 page images (benefits table, ER/urgent care, drug costs).
- **Answer key:** CMS's 2027 Plan Benefit Package data. Premium is left out, because the documents show the total premium while CMS records only the medical part.

| | Clairic, text | Haiku 5.5, text | Clairic, images | Haiku 5.5, images |
|---|---|---|---|---|
| Field accuracy | 100% | 100% | 97.2% | 99.2% |
| Drug deductible left empty when the plan has no drug coverage | 0 of 13 | 13 of 13 | 0 of 13 | 13 of 13 |
| Median time | 0.76 s | 1.90 s | 2.54 s | 1.85 s |
| Cost per 1,000 plans | $1.18 | $1.94 | $0.49 | $0.89 |

Clairic's misses on images were ER copays, where it read a nearby amount ($325 instead of $130). All 27 came with low confidence (0.02–0.51).
