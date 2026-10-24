// Built-in test cases. Each one sends a fixed input to Clairic and checks the
// answers it should get. Answers are known in advance for extraction and
// computation; estimates and forecasts get looser range checks.
import type { ClairicRequest } from "./clairic.ts";
import type { Check } from "./evaluate.ts";

export interface TestCase {
  id: string;
  title: string;
  /** What this case probes. */
  about: string;
  request: Omit<ClairicRequest, "images" | "model">;
  /** Image files from assets/, attached to the request in order. */
  imageAssets?: string[];
  expect?: Record<string, Check>;
}

// --- Synthetic daily series for the forecast case -------------------------
// Steady upward trend with a weekly pattern (busy Fridays, quiet weekends).
// The "right" next values come from the same formula, so we can score them.
const WEEKDAY_FACTOR = [0.65, 1.0, 0.97, 0.98, 1.03, 1.12, 0.72]; // Sun..Sat
const SERIES_START = Date.UTC(2026, 8, 1); // 2026-09-01
const HISTORY_DAYS = 42;
const FORECAST_DAYS = 7;
const DAY_MS = 86_400_000;

function seriesValue(dayIndex: number): number {
  const dow = new Date(SERIES_START + dayIndex * DAY_MS).getUTCDay();
  return Math.round((1200 + 4 * dayIndex) * WEEKDAY_FACTOR[dow]!);
}
function isoDay(dayIndex: number): string {
  return new Date(SERIES_START + dayIndex * DAY_MS).toISOString().slice(0, 10);
}

const forecastHistory = [
  "date,orders",
  ...Array.from({ length: HISTORY_DAYS }, (_, i) => `${isoDay(i)},${seriesValue(i)}`),
  "",
  `Forecast dates: ${isoDay(HISTORY_DAYS)} through ${isoDay(HISTORY_DAYS + FORECAST_DAYS - 1)}`,
].join("\n");

export const forecastTruth = Array.from({ length: FORECAST_DAYS }, (_, i) => seriesValue(HISTORY_DAYS + i));

// --- Cases ----------------------------------------------------------------
export const CASES: TestCase[] = [
  {
    id: "email_complaint",
    title: "Customer complaint email",
    about:
      "Copies values out of the email, adds price + tax, counts days between two dates, lists the damage, and should return null for a tracking number that isn't there.",
    request: {
      reference_date: "2026-10-20",
      state: `From: Dana Whitfield <dana.w@example.com>
Date: Tue, 20 Oct 2026
Subject: Damaged order A-55821

Hi there,

My order A-55821 arrived on October 14 and the box was crushed. The ceramic lamp base is cracked and the shade has a big dent in it. I paid $84.99 plus $7.01 tax.

I'd like a refund or a replacement, whichever is faster.

Thanks,
Dana`,
      fields: {
        customer_name: { type: "text", instructions: "Full name of the customer" },
        order_id: { type: "text", instructions: "The order identifier" },
        delivered_on: { type: "date", instructions: "Date the order was delivered" },
        amount_paid: { type: "number", decimals: 2, unit: "USD", instructions: "Total paid including tax" },
        days_since_delivery: {
          type: "duration",
          unit: "days",
          integer: true,
          instructions: "Days from delivery until the email was sent",
        },
        damage: { type: "list", items: "text", instructions: "Each damaged item or problem described" },
        tracking_number: { type: "text" },
      },
    },
    expect: {
      customer_name: { contains: "Dana Whitfield" },
      order_id: { contains: "A-55821" },
      delivered_on: { equals: "2026-10-14" },
      amount_paid: { equals: 92.0 },
      days_since_delivery: { equals: 6 },
      damage: { contains: "lamp" },
      tracking_number: { isNull: true },
    },
  },
  {
    id: "invoice",
    title: "Invoice with payment terms",
    about: "Example from Cloudflare's docs, plus a due date computed from 'net 30' and a PO number that isn't present.",
    request: {
      state: "Invoice INV-2291 from Northwind Supply, issued 2026-09-02, net 30. Total $1,080.00.",
      fields: {
        invoice_number: { type: "text" },
        vendor: { type: "text", instructions: "Company that issued the invoice" },
        total: { type: "number", decimals: 2, unit: "USD" },
        due_date: { type: "date", instructions: "Date payment is due under the stated terms" },
        po_number: { type: "text", instructions: "Purchase order number" },
        days_to_pay: {
          type: "estimate",
          min: 0,
          max: 120,
          step: 1,
          instructions: "Days after issue until this invoice is paid",
        },
      },
    },
    expect: {
      invoice_number: { equals: "INV-2291" },
      vendor: { contains: "Northwind" },
      total: { equals: 1080 },
      due_date: { equals: "2026-10-02" },
      po_number: { isNull: true },
      days_to_pay: { between: [0, 120] },
    },
  },
  {
    id: "receipt_image",
    title: "Receipt photo (image input)",
    about: "Reads a receipt from an image alone (no text). The discount line doesn't exist, so it should come back null.",
    imageAssets: ["receipt.png"],
    request: {
      reference_date: "2026-10-24",
      fields: {
        order_number: { type: "text", instructions: "Order or check number" },
        purchased_at: { type: "datetime", instructions: "Date and time of purchase" },
        quantity: { type: "number", integer: true, instructions: "Quantity of the drink ordered" },
        unit_price: { type: "number", decimals: 2, unit: "USD", instructions: "Price of one drink" },
        subtotal: { type: "number", decimals: 2, unit: "USD" },
        sales_tax: { type: "number", decimals: 2, unit: "USD" },
        tip: { type: "number", decimals: 2, unit: "USD" },
        total: { type: "number", decimals: 2, unit: "USD", instructions: "Grand total charged" },
        discount: { type: "number", decimals: 2, unit: "USD", instructions: "Discount or coupon amount" },
      },
    },
    expect: {
      order_number: { contains: "1047" },
      purchased_at: { matches: "2026-10-18[T ]19:42" },
      quantity: { equals: 2 },
      unit_price: { equals: 5.25 },
      subtotal: { equals: 10.5 },
      sales_tax: { equals: 0.92 },
      tip: { equals: 2.0 },
      total: { equals: 13.42 },
      discount: { isNull: true },
    },
  },
  {
    id: "schedule",
    title: "Meeting times and durations",
    about: "Resolves a date with no year, a 'next morning' reference, and converts '90 minutes' into a duration.",
    request: {
      reference_date: "2026-10-24",
      state:
        "Quick update: the kickoff call is Tuesday, Nov 3 at 2:30 PM and should run about 90 minutes. We set a follow-up for the next morning at 9.",
      fields: {
        kickoff_start: { type: "datetime", instructions: "When the kickoff call starts" },
        kickoff_length: { type: "duration", unit: "minutes", integer: true },
        followup_date: { type: "date", instructions: "Date of the follow-up" },
        followup_time: { type: "time", instructions: "Start time of the follow-up" },
      },
    },
    expect: {
      kickoff_start: { matches: "2026-11-03[T ]14:30" },
      kickoff_length: { equals: 90 },
      followup_date: { equals: "2026-11-04" },
      followup_time: { matches: "^0?9:00" },
    },
  },
  {
    id: "null_handling",
    title: "Values that aren't there",
    about: "One real value next to several that are mentioned but not given. Checks that Clairic returns null instead of guessing.",
    request: {
      state:
        "Thanks for your patience. We refunded $12.50 for the shipping charge. Your replacement will ship this week, and we'll email the tracking number once the carrier assigns one.",
      fields: {
        refund_amount: { type: "number", decimals: 2, unit: "USD" },
        tracking_number: { type: "text" },
        carrier: { type: "text", instructions: "Shipping carrier name" },
        coupon_code: { type: "text" },
      },
    },
    expect: {
      refund_amount: { equals: 12.5 },
      tracking_number: { isNull: true },
      carrier: { isNull: true },
      coupon_code: { isNull: true },
    },
  },
  {
    id: "math_chain",
    title: "Multi-step arithmetic (known weak spot)",
    about:
      "Cloudflare says longer chains of math aren't reliable yet. Subtotal needs three multiplications and a sum; total depends on that plus 8% tax.",
    request: {
      state: `Order summary
3 x Spiral notebook @ $4.25
2 x Gel pen @ $1.10
1 x Canvas backpack @ $39.00
Sales tax rate: 8%`,
      fields: {
        item_count: { type: "number", integer: true, instructions: "Total number of units ordered" },
        subtotal: { type: "number", decimals: 2, unit: "USD", instructions: "Sum of all line items before tax" },
        tax: { type: "number", decimals: 2, unit: "USD", instructions: "Sales tax on the subtotal" },
        total: { type: "number", decimals: 2, unit: "USD", instructions: "Subtotal plus tax" },
      },
    },
    expect: {
      item_count: { equals: 6 },
      subtotal: { equals: 53.95 },
      tax: { equals: 4.32 },
      total: { equals: 58.27 },
    },
  },
  {
    id: "home_price",
    title: "Home price estimate from comparables",
    about: "Estimate with an 80% range. The subject house sits between the most similar sales ($405k–$418k).",
    request: {
      state: `Recent comparable sales:
address,beds,baths,sqft,lot_sqft,year_built,sale_date,sale_price
12 Oak St,3,2,1650,6000,1998,2026-07-12,405000
48 Elm Ave,3,2,1720,6500,2001,2026-06-03,418000
7 Birch Ct,4,2.5,1980,7200,2004,2026-08-21,462000
101 Pine Rd,3,1.5,1500,5800,1992,2026-05-15,372000
33 Cedar Ln,3,2,1690,6100,1999,2026-09-02,412000

Subject property: 22 Maple Dr, 3 beds, 2 baths, 1,700 sqft, 6,200 sqft lot, built 2000. Listing now.`,
      fields: {
        sale_price: {
          type: "estimate",
          unit: "USD",
          min: 50000,
          max: 5000000,
          scale: "log",
          step: 1000,
          instructions: "Likely sale price of the subject property",
        },
      },
    },
    expect: {
      sale_price: { between: [385000, 435000] },
    },
  },
  {
    id: "forecast",
    title: "7-day forecast of a weekly pattern",
    about:
      "Six weeks of synthetic daily orders with a trend and a weekly cycle. The correct next 7 values come from the same formula; checks each forecast step within 10%.",
    request: {
      state: forecastHistory,
      fields: {
        orders: {
          type: "forecast",
          horizon: FORECAST_DAYS,
          interval: "day",
          min: 0,
          max: 5000,
          instructions: "Daily orders for each forecast date",
        },
      },
    },
    expect: {
      orders: { near: forecastTruth, tolerance: 0.1 },
    },
  },
];

export function findCase(id: string): TestCase | undefined {
  return CASES.find((c) => c.id === id);
}
