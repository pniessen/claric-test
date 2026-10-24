// Renders assets/receipt.png, the image used by the "receipt_image" test case.
// Uses Playwright's Chromium. Run: npm run build:receipt
import { createRequire } from "node:module";
// require() (unlike import) honors NODE_PATH, so a globally installed Playwright works too.
const { chromium } = createRequire(import.meta.url)("playwright");
import { fileURLToPath } from "node:url";

const out = fileURLToPath(new URL("../assets/receipt.png", import.meta.url));

const html = `<!doctype html><html><body style="margin:0;background:#8a7f72;width:520px;height:760px;display:flex;align-items:center;justify-content:center">
<div style="transform:rotate(-2.5deg);background:#fbf8f1;width:340px;padding:28px 26px;box-shadow:0 6px 18px rgba(0,0,0,.35);
  font:15px/1.45 'Courier New',monospace;color:#2b2b2b;filter:blur(.35px) contrast(.92)">
  <div style="text-align:center;font-weight:bold;font-size:19px">BLUE HERON COFFEE</div>
  <div style="text-align:center">418 Harbor St, Portland OR</div>
  <div style="text-align:center">(503) 555-0142</div>
  <hr style="border:0;border-top:1px dashed #555">
  <div>Order #1047</div>
  <div>10/18/2026&nbsp;&nbsp;&nbsp;7:42 PM</div>
  <div>Server: Maya&nbsp;&nbsp;&nbsp;Table: 6</div>
  <hr style="border:0;border-top:1px dashed #555">
  <div style="display:flex;justify-content:space-between"><span>2 Iced Latte @ 5.25</span><span>10.50</span></div>
  <hr style="border:0;border-top:1px dashed #555">
  <div style="display:flex;justify-content:space-between"><span>Subtotal</span><span>10.50</span></div>
  <div style="display:flex;justify-content:space-between"><span>Sales Tax</span><span>0.92</span></div>
  <div style="display:flex;justify-content:space-between"><span>Tip</span><span>2.00</span></div>
  <div style="display:flex;justify-content:space-between;font-weight:bold;font-size:17px"><span>TOTAL</span><span>$13.42</span></div>
  <hr style="border:0;border-top:1px dashed #555">
  <div>VISA ****4417&nbsp;&nbsp;APPROVED</div>
  <div style="text-align:center;margin-top:14px">Thank you! Come again.</div>
</div></body></html>`;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 520, height: 760 } });
await page.setContent(html);
await page.screenshot({ path: out });
await browser.close();
console.log("wrote", out);
