// Single-page UI served at "/". Plain HTML + JS, no build step.
export const PAGE_HTML = /* html */ `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Clairic Test Bench</title>
<style>
  :root {
    --bg: #fafaf9; --panel: #ffffff; --text: #1c1917; --muted: #78716c; --line: #e7e5e4;
    --accent: #ea580c; --pass: #15803d; --pass-bg: #dcfce7; --fail: #b91c1c; --fail-bg: #fee2e2;
    --code-bg: #f5f5f4;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0c0a09; --panel: #1c1917; --text: #f5f5f4; --muted: #a8a29e; --line: #292524;
      --accent: #fb923c; --pass: #4ade80; --pass-bg: #052e16; --fail: #f87171; --fail-bg: #450a0a;
      --code-bg: #0c0a09;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
    font: 15px/1.5 system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
  main { max-width: 960px; margin: 0 auto; padding: 24px 16px 64px; }
  h1 { font-size: 1.5rem; margin: 0 0 4px; }
  h2 { font-size: 1.1rem; margin: 32px 0 12px; }
  p.sub { color: var(--muted); margin: 0 0 16px; }
  .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 14px; margin-bottom: 12px; }
  .row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  button { font: inherit; border: 1px solid var(--accent); background: var(--accent); color: #fff;
    border-radius: 8px; padding: 6px 14px; cursor: pointer; }
  button.ghost { background: transparent; color: var(--accent); }
  button:disabled { opacity: .5; cursor: wait; }
  input, textarea { font: 13px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace; width: 100%;
    background: var(--code-bg); color: var(--text); border: 1px solid var(--line); border-radius: 8px; padding: 8px; }
  label { display: block; font-size: .85rem; color: var(--muted); margin: 10px 0 4px; }
  .case-head { display: flex; justify-content: space-between; gap: 8px; align-items: flex-start; }
  .case-head h3 { margin: 0; font-size: 1rem; }
  .about { color: var(--muted); font-size: .9rem; margin: 4px 0 0; }
  .badge { display: inline-block; font-size: .8rem; padding: 1px 8px; border-radius: 999px; white-space: nowrap; }
  .badge.pass { background: var(--pass-bg); color: var(--pass); }
  .badge.fail { background: var(--fail-bg); color: var(--fail); }
  .meta { color: var(--muted); font-size: .85rem; }
  .table-wrap { overflow-x: auto; margin-top: 10px; }
  table { border-collapse: collapse; width: 100%; min-width: 560px; font-size: .85rem; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { color: var(--muted); font-weight: 500; }
  td.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; overflow-wrap: anywhere; }
  td.name { white-space: nowrap; }
  td.ok { color: var(--pass); } td.bad { color: var(--fail); }
  details { margin-top: 8px; } summary { cursor: pointer; color: var(--muted); font-size: .85rem; }
  pre { background: var(--code-bg); border: 1px solid var(--line); border-radius: 8px; padding: 10px;
    overflow: auto; font-size: 12px; max-height: 420px; }
  .summary { font-size: 1rem; }
  .err { color: var(--fail); white-space: pre-wrap; }
</style>
</head>
<body>
<main>
  <h1>Clairic Test Bench</h1>
  <p class="sub">Runs test inputs through <code>@cf/cloudflare/clairic</code> on Workers AI and checks each answer.</p>

  <div class="panel" id="token-panel" hidden>
    <label for="token">Access token (this Worker has ACCESS_TOKEN set)</label>
    <div class="row"><input id="token" type="password" autocomplete="off" style="flex:1"><button id="save-token">Save</button></div>
  </div>

  <div class="row">
    <button id="run-all">Run all cases</button>
    <span class="summary" id="all-summary"></span>
  </div>

  <h2>Built-in cases</h2>
  <div id="cases"></div>

  <h2>Playground</h2>
  <div class="panel">
    <label for="pg-state">Text (state)</label>
    <textarea id="pg-state" rows="6">Invoice INV-2291 from Northwind Supply, issued 2026-09-02, net 30. Total $1,080.00.</textarea>
    <label for="pg-fields">Fields (JSON)</label>
    <textarea id="pg-fields" rows="10">{
  "invoice_number": { "type": "text" },
  "total": { "type": "number", "decimals": 2, "unit": "USD" },
  "due_date": { "type": "date", "instructions": "Date payment is due" }
}</textarea>
    <label for="pg-ref">Reference date (optional, YYYY-MM-DD)</label>
    <input id="pg-ref" placeholder="2026-10-24">
    <label for="pg-images">Images (optional, up to 4 PNG/JPEG/WebP, 4 MB each)</label>
    <input id="pg-images" type="file" accept="image/png,image/jpeg,image/webp" multiple>
    <div class="row" style="margin-top:12px"><button id="pg-run">Extract</button><span class="meta" id="pg-meta"></span></div>
    <div id="pg-out"></div>
  </div>
</main>
<script>
const $ = (s) => document.querySelector(s);
const el = (tag, props = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") n.className = v; else if (k === "text") n.textContent = v; else n.setAttribute(k, v);
  }
  for (const k of kids) if (k != null) n.append(k);
  return n;
};
const fmt = (v) => v === undefined ? "" : typeof v === "string" ? v : JSON.stringify(v);
const pct = (v) => typeof v === "number" ? (v * 100).toFixed(1) + "%" : "";
const usd = (v) => typeof v === "number" ? "$" + v.toFixed(6) : "";

let token = "";
try { token = localStorage.getItem("clairic-token") || ""; } catch {}
$("#token").value = token;
$("#save-token").onclick = () => {
  token = $("#token").value.trim();
  try { localStorage.setItem("clairic-token", token); } catch {}
  loadCases();
};

async function api(path, opts = {}) {
  const headers = { "content-type": "application/json", ...(opts.headers || {}) };
  if (token) headers.authorization = "Bearer " + token;
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401) { $("#token-panel").hidden = false; throw new Error("Unauthorized: enter the access token above."); }
  const data = await res.json().catch(() => ({ error: "non-JSON response (" + res.status + ")" }));
  if (!res.ok && data.error) throw new Error(data.error + (data.problems ? "\\n" + data.problems.join("\\n") : ""));
  return data;
}

function answersTable(answers) {
  const rows = Object.entries(answers || {}).map(([name, a]) => {
    let value = fmt(a.value);
    if (a.type === "estimate" || a.type === "forecast") {
      value = "median " + fmt(a.median) + "  (80%: " + fmt(a.p10) + " – " + fmt(a.p90) + ")";
    }
    const guess = a.value === null && a.guess !== undefined ? " (guess: " + fmt(a.guess) + ")" : "";
    return el("tr", {},
      el("td", { class: "mono name", text: name }),
      el("td", { text: a.type }),
      el("td", { class: "mono", text: value + guess }),
      el("td", { text: pct(a.confidence) }),
      el("td", { text: pct(a.p_null) }));
  });
  return el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ...["Field", "Type", "Answer", "Confidence", "p_null"].map((h) => el("th", { text: h })))),
    el("tbody", {}, ...rows)));
}

function checksTable(checks) {
  return el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ...["", "Field", "Expected", "Got"].map((h) => el("th", { text: h })))),
    el("tbody", {}, ...checks.map((c) => el("tr", {},
      el("td", { class: c.pass ? "ok" : "bad", text: c.pass ? "✓" : "✗" }),
      el("td", { class: "mono name", text: c.field }),
      el("td", { class: "mono", text: c.expected }),
      el("td", { class: "mono", text: c.got }))))));
}

function renderResult(box, r) {
  box.replaceChildren();
  if (r.error) { box.append(el("p", { class: "err", text: r.error })); return; }
  box.append(el("p", { class: "meta", text:
    r.passed + "/" + r.total + " checks passed · " + r.latency_ms + " ms · " +
    (r.response?.usage?.input_tokens ?? "?") + " input tokens · " + usd(r.cost_usd) }));
  box.append(checksTable(r.checks));
  box.append(el("details", {}, el("summary", { text: "All answers" }), answersTable(r.response?.answers)));
  box.append(el("details", {}, el("summary", { text: "Raw response" }), el("pre", { text: JSON.stringify(r.response, null, 2) })));
}

const caseBoxes = {};
async function loadCases() {
  const list = $("#cases");
  let cases;
  try { cases = await api("/api/cases"); } catch (e) { list.replaceChildren(el("p", { class: "err", text: e.message })); return; }
  list.replaceChildren();
  for (const c of cases) {
    const badge = el("span", { class: "badge" });
    const btn = el("button", { class: "ghost", text: "Run" });
    const out = el("div");
    const input = el("details", {}, el("summary", { text: "Input" }),
      c.imageAssets.length ? el("img", { src: "/assets/" + c.imageAssets[0], alt: "Test image", style: "max-width:240px;display:block;margin:8px 0;border-radius:6px" }) : null,
      el("pre", { text: JSON.stringify(c.request, null, 2) }));
    const panel = el("div", { class: "panel" },
      el("div", { class: "case-head" },
        el("div", {}, el("h3", { text: c.title }), el("p", { class: "about", text: c.about })),
        el("div", { class: "row" }, badge, btn)),
      input, out);
    caseBoxes[c.id] = { badge, btn, out };
    btn.onclick = () => runOne(c.id);
    list.append(panel);
  }
}

function setBadge(badge, r) {
  badge.className = "badge " + (r.ok ? "pass" : "fail");
  badge.textContent = r.error ? "error" : r.passed + "/" + r.total;
}

async function runOne(id) {
  const { badge, btn, out } = caseBoxes[id];
  btn.disabled = true; out.replaceChildren(el("p", { class: "meta", text: "Running…" }));
  try { const r = await api("/api/cases/" + id + "/run", { method: "POST" }); setBadge(badge, r); renderResult(out, r); }
  catch (e) { out.replaceChildren(el("p", { class: "err", text: e.message })); }
  finally { btn.disabled = false; }
}

$("#run-all").onclick = async () => {
  const btn = $("#run-all"); btn.disabled = true;
  $("#all-summary").textContent = "Running all cases…";
  try {
    const s = await api("/api/cases/run-all", { method: "POST" });
    for (const r of s.results) { const b = caseBoxes[r.id]; if (b) { setBadge(b.badge, r); renderResult(b.out, r); } }
    $("#all-summary").textContent = s.cases_passed + "/" + s.cases_total + " cases, " +
      s.checks_passed + "/" + s.checks_total + " checks passed · total cost " + usd(s.total_cost_usd);
  } catch (e) { $("#all-summary").textContent = e.message; }
  finally { btn.disabled = false; }
};

function fileToDataUrl(f) {
  return new Promise((resolve, reject) => {
    const r = new FileReader(); r.onload = () => resolve(r.result); r.onerror = reject; r.readAsDataURL(f);
  });
}

$("#pg-run").onclick = async () => {
  const out = $("#pg-out"), meta = $("#pg-meta"), btn = $("#pg-run");
  out.replaceChildren(); meta.textContent = "";
  let fields;
  try { fields = JSON.parse($("#pg-fields").value); } catch (e) { out.append(el("p", { class: "err", text: "Fields JSON: " + e.message })); return; }
  const body = { state: $("#pg-state").value, fields };
  const ref = $("#pg-ref").value.trim(); if (ref) body.reference_date = ref;
  const files = [...$("#pg-images").files].slice(0, 4);
  if (files.length) body.images = await Promise.all(files.map(fileToDataUrl));
  btn.disabled = true; meta.textContent = "Running…";
  try {
    const r = await api("/api/extract", { method: "POST", body: JSON.stringify(body) });
    meta.textContent = r.latency_ms + " ms · " + (r.response?.usage?.input_tokens ?? "?") + " input tokens · " + usd(r.cost_usd);
    out.append(answersTable(r.response.answers));
    out.append(el("details", {}, el("summary", { text: "Raw response" }), el("pre", { text: JSON.stringify(r.response, null, 2) })));
  } catch (e) { meta.textContent = ""; out.append(el("p", { class: "err", text: e.message })); }
  finally { btn.disabled = false; }
};

loadCases();
</script>
</body>
</html>`;
