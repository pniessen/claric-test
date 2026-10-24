# claric-test

A test bench for [Clairic](https://blog.cloudflare.com/clairic-extraction-model/), Cloudflare's extraction model on Workers AI (`@cf/cloudflare/clairic`, in preview).

It's a Cloudflare Worker with:

- **Built-in test cases** that send fixed inputs to Clairic and check each answer against the expected value.
- **A playground page** where you paste text, write a field schema, attach up to 4 images, and see the answers with confidence scores.
- **A command-line runner** that runs the same cases through the REST API, so you can test without deploying.

It also benchmarks Clairic against Claude Haiku on public document datasets: see the summary below and [`bench/`](bench/README.md).

## Results: Clairic vs Claude Haiku 5.5

Measured on 2026-10-24 with 1,462 documents from 8 test sets. Both models got the same document, field names and instructions, and no calls failed.

| Test set | What it is | Docs | Accuracy, Clairic | Accuracy, Haiku 5.5 | Median time, Clairic | Median time, Haiku 5.5 | $ per 1k docs, Clairic | $ per 1k docs, Haiku 5.5 |
|---|---|---|---|---|---|---|---|---|
| SROIE | Scanned Malaysian receipts (image): company, date, address, total | 347 | 47.4% | **89.0%** | **0.7 s** | 1.4 s | 0.34 | **0.30** |
| CORD v2 | Photographed Indonesian receipts (image): totals, tax, cash, change, items | 100 | 91.5% | **95.9%** | **0.5 s** | 1.7 s | **0.18** | 0.43 |
| ACA health plans, images | Summary of Benefits and Coverage, 4 page images: deductibles, out-of-pocket limits, copays, coverage examples | 150 | 93.8% | **95.1%** | 2.8 s | **2.1 s** | **0.54** | 1.00 |
| ACA health plans, text | The same documents as text | 150 | 86.0% | **98.4%** | **0.6 s** | 2.2 s | **0.74** | 1.41 |
| Medicare Advantage, images | Humana 2027 Summary of Benefits, up to 4 page images: out-of-pocket max, deductibles, copays, hospital and drug costs | 96 | 97.2% | **99.2%** | 2.5 s | **1.9 s** | **0.49** | 0.89 |
| Medicare Advantage, text | The same documents as text | 96 | **100%** | **100%** | **0.8 s** | 1.9 s | **1.18** | 1.94 |
| Kleister NDA | SEC non-disclosure agreements (text): effective date, jurisdiction, term | 83 | 88.5% | **94.5%** | **0.7 s** | 2.1 s | **0.73** | 0.77 |
| Kleister Charity | UK charity annual reports, often long (text): income, spending, dates, names, address | 440 | **82.8%** | 80.6% | **1.8 s** | 2.5 s | 3.15 | **2.04** |

**How to read it**
- **Accuracy** is the average share of fields matching the answer key, over each test set's single-value fields. Text must match exactly, ignoring case and punctuation. Dates are compared as dates and amounts to the cent; Charity amounts count if within £1, because its answer key mostly drops pence.
- **Time** is the full round trip from a cloud machine, with several requests running at once.
- **Cost** is list price: Clairic and Haiku 5.5 are both $0.10 per million input tokens, and Haiku also charges $0.50 per million output tokens. All 8 test sets together cost $1.94 on Clairic and $1.74 on Haiku 5.5.

**What we found**
- **Haiku 5.5 was more accurate on 6 of the 8 test sets.** They tied on Medicare text, and Clairic led on the long Charity reports.
- **Clairic is about 2–3 times faster on text and single images,** usually under a second. With several page images per request it was slower than Haiku.
- **Clairic struggles to read longer text from images, and to fill list fields.** It got 2% of SROIE company names exactly right (Haiku 76%) and 17% F1 on NDA parties (Haiku 90%). Its list answers contain many fragments.
- **Clairic rarely leaves a field empty when the document doesn't give it.** For example, it returned $0 for a copay on plans that charge coinsurance instead. On health-plan text it left 21% of such fields empty; Haiku 5.5 left 83%.
- **Clairic's confidence score is useful.** Answers it rated 0.9 or higher were at least 93.7% right on every test set. On Medicare images, all 27 of its wrong answers scored 0.51 or lower.
- **On health-plan PDFs, images beat our text conversion for Clairic,** because converting tables to text loses the columns. Clairic doesn't accept PDFs, so convert pages to PNG or JPEG (at most 4 per request).
- **Claude Haiku 4.5** is also in [`bench/README.md`](bench/README.md). It was less accurate than Haiku 5.5 and cost about 7 times as much.

The answer keys come from the datasets' own labels (SROIE, CORD, Kleister) or from CMS's public plan data (ACA Marketplace 2026 and Medicare Advantage 2027 Plan Benefit Package files). The health-plan documents belong to the insurers, so they are downloaded by the scripts, not committed. Each test set's setup, scoring rules and caveats are in [`bench/README.md`](bench/README.md).

## Test cases

| Case | What it checks |
|---|---|
| `email_complaint` | Copies values from an email, adds price + tax, counts days between dates, lists damage, returns null for a missing tracking number |
| `invoice` | Cloudflare's docs example, plus a "net 30" due date and a missing PO number |
| `receipt_image` | Reads a receipt from an image only ([assets/receipt.png](assets/receipt.png)), returns null for a discount that isn't there |
| `schedule` | A date with no year, "the next morning", and a duration in minutes |
| `null_handling` | Values that are mentioned but not given should come back null, not guessed |
| `math_chain` | Multi-step arithmetic, which Cloudflare lists as a known weak spot |
| `home_price` | Estimate with an 80% range from comparable sales |
| `forecast` | 7-day forecast of a synthetic series with a trend and a weekly cycle |

Extraction and computation cases have exact expected answers. Estimate and forecast checks are looser ranges, so a failure there is a signal to look closer, not necessarily a wrong answer.

## Setup

Needs Node 22.18+ (the scripts run `.ts` files directly).

```sh
npm install
npx wrangler login
```

## Option A: deploy the Worker

```sh
npm run deploy
```

Wrangler prints the URL (`https://clairic-test.<your-subdomain>.workers.dev`). Open it and press **Run all cases**.

Once deployed, the Worker URL is public, and every request spends your Workers AI budget. To lock it down, set an access token. The page will ask for it once and remember it in that browser:

```sh
npx wrangler secret put ACCESS_TOKEN
```

You can also put the Worker behind Cloudflare Access instead.

To run it locally against the real model: `npm run dev`. The AI binding always calls Cloudflare, so this needs `wrangler login`.

## Option B: run from the command line (no deploy)

Create an API token with the **Workers AI: Read** permission (Dashboard → My Profile → API Tokens) and set `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN`. On a Mac you can keep the token in the login keychain instead: set `CF_KEYCHAIN_SERVICE` to the entry's name. Then:

```sh
npm run cases                                 # all cases
npm run cases -- receipt_image math_chain     # chosen cases
npm run cases -- --json > results.json        # full JSON output
```

## API

All `/api` routes need `Authorization: Bearer <ACCESS_TOKEN>` if that secret is set.

| Route | Does |
|---|---|
| `GET /api/cases` | Lists the built-in cases |
| `POST /api/cases/:id/run` | Runs one case and returns checks, latency, tokens and cost |
| `POST /api/cases/run-all` | Runs every case in turn and returns a summary |
| `POST /api/extract` | Sends your own request. The body uses the same format as Clairic's API: `state`, `fields`, `images`, `reference_date`, `options` |

Example:

```sh
curl -X POST https://clairic-test.<sub>.workers.dev/api/extract \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"state":"Invoice INV-2291, total $1,080.00","fields":{"total":{"type":"number","decimals":2,"unit":"USD"}}}'
```

## Development

```sh
npm test            # offline unit tests (scoring logic, request checks, runner)
npm run typecheck
npm run build:receipt   # re-render assets/receipt.png (needs Playwright)
```

| File | Contents |
|---|---|
| `src/clairic.ts` | Request/response types from Cloudflare's published schemas, a cost estimate, and request validation |
| `src/cases.ts` | The test cases and their expected answers. Add new cases here |
| `src/evaluate.ts` | Check types: `equals`, `isNull`, `contains`, `matches`, `between`, `near` |
| `src/runner.ts` | Runs a case and scores it. Shared by the Worker and the CLI |
| `src/index.ts` | Worker routes |
| `src/page.ts` | The browser UI |

Pricing used for cost figures: $0.10 per million input tokens, with no charge for output tokens (from the [model page](https://developers.cloudflare.com/workers-ai/models/clairic/)).
