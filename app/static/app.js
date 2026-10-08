"use strict";

const state = { requests: [], selected: null, architecture: "single", details: {} };

const DANGER_FLAGS = new Set([
  "budget_insufficient", "prompt_injection_detected", "conflicting_vendor_evidence",
  "vendor_risk_unavailable", "vendor_review_expired",
]);

const fmtUsd = (v) =>
  v == null ? "—" : "$" + Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 });
const tit=(s)=> (s||"").replace(/_/g," ").replace(/\b\w/g, c=>c.toUpperCase());
// Escape untrusted text before it goes into innerHTML (evidence findings include
// LLM-generated text; business data is never trusted as markup).
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function getJSON(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) throw new Error((await res.json().catch(()=>({}))).detail || res.statusText);
  return res.json();
}

async function init() {
  try {
    const meta = await getJSON("/api/meta");
    document.getElementById("meta-provider").textContent = "LLM: " + meta.llm_provider;
    document.getElementById("meta-date").textContent = "as of " + meta.reference_date;
  } catch (_) {}

  state.requests = await getJSON("/api/requests");
  document.getElementById("request-count").textContent = state.requests.length + " total";
  renderList();

  document.querySelectorAll(".arch-btn").forEach((btn) =>
    btn.addEventListener("click", () => {
      state.architecture = btn.dataset.arch;
      document.querySelectorAll(".arch-btn").forEach((b) => b.classList.toggle("is-active", b === btn));
    })
  );
  document.getElementById("run-btn").addEventListener("click", runDecision);
}

function renderList() {
  const ul = document.getElementById("request-list");
  ul.innerHTML = "";
  for (const r of state.requests) {
    const li = document.createElement("li");
    li.className = "request-item";
    li.dataset.id = r.request_id;
    li.innerHTML = `
      <div class="ri-top">
        <span class="ri-product">${esc(r.product_name ?? "—")}</span>
        <span class="ri-cost">${fmtUsd(r.annual_cost_usd)}</span>
      </div>
      <div class="ri-sub">${esc(r.vendor_name ?? "—")} · ${esc(r.department ?? "—")}</div>
      <div class="ri-sub"><span class="ri-id">${esc(r.request_id)}</span> · ${esc(r.requester ?? "—")}</div>`;
    li.addEventListener("click", () => selectRequest(r.request_id));
    ul.appendChild(li);
  }
}

async function selectRequest(id) {
  state.selected = id;
  document.querySelectorAll(".request-item").forEach((el) =>
    el.classList.toggle("is-active", el.dataset.id === id));
  document.getElementById("empty-state").hidden = true;
  document.getElementById("detail").hidden = false;
  document.getElementById("results").hidden = true;

  const d = state.details[id] || (state.details[id] = await getJSON(`/api/requests/${id}`));
  document.getElementById("detail-title").textContent = `${d.product_name} — ${d.vendor_name}`;
  const urgency = document.getElementById("detail-urgency");
  urgency.textContent = d.urgency || "normal";
  urgency.className = "pill " + (d.urgency || "normal");

  const facts = [
    ["Request ID", d.request_id],
    ["Category", d.category],
    ["Annual cost", fmtUsd(d.annual_cost_usd)],
    ["Users / licenses", d.user_count ?? "—"],
    ["Data access", d.data_access_level],
    ["Integrations", (d.requested_integrations || []).join(", ") || "none"],
  ];
  document.getElementById("detail-facts").innerHTML = facts
    .map(([k, v]) => `<div class="fact"><dt>${esc(k)}</dt><dd>${esc(v ?? "—")}</dd></div>`).join("");
  document.getElementById("detail-justification").textContent = d.business_justification || "—";
}

async function runDecision() {
  if (!state.selected) return;
  const btn = document.getElementById("run-btn");
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span>Analyzing…`;
  try {
    const dec = await getJSON("/api/decide", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request_id: state.selected, architecture: state.architecture }),
    });
    renderResults(dec);
  } catch (e) {
    alert("Analysis failed: " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Analyze request";
  }
}

function renderResults(dec) {
  const results = document.getElementById("results");
  results.hidden = false;

  // Recommendation tone
  const rec = document.getElementById("recommendation");
  let tone = "tone-ok";
  if (dec.missing_information.length) tone = "tone-warn";
  else if (dec.risk_flags.some((f) => DANGER_FLAGS.has(f))) tone = "tone-danger";
  else if (dec.risk_flags.length) tone = "tone-warn";
  rec.className = "recommendation " + tone;
  document.getElementById("rec-text").textContent = dec.recommendation;
  document.getElementById("rec-next-text").textContent = dec.next_step;
  document.getElementById("rec-human").textContent = dec.human_review_required
    ? "Human review required — the copilot recommends only; a person makes the final decision."
    : "No human review required.";

  // Approvals
  const ap = document.getElementById("approvals");
  ap.innerHTML = dec.required_approvals.length
    ? dec.required_approvals.map((a) => `<span class="chip approval">${esc(a)}</span>`).join("")
    : `<span class="chip-empty">None required</span>`;

  // Risk flags
  const rf = document.getElementById("risk-flags");
  rf.innerHTML = dec.risk_flags.length
    ? dec.risk_flags.map((f) => {
        const cls = DANGER_FLAGS.has(f) ? "risk-danger" : "risk-warn";
        return `<span class="chip ${cls}">${esc(tit(f))}</span>`;
      }).join("")
    : `<span class="chip-empty">No flags</span>`;

  // Missing info
  const mi = document.getElementById("missing-info");
  mi.innerHTML = dec.missing_information.length
    ? dec.missing_information.map((m) => `<li>${esc(m)}</li>`).join("")
    : `<li class="chip-empty">Nothing missing</li>`;

  // Evidence (findings may include LLM-generated text -> escape before render)
  document.getElementById("evidence-count").textContent = `(${dec.evidence.length})`;
  document.getElementById("evidence-body").innerHTML = dec.evidence
    .map((e) => `<tr><td class="src">${esc(e.source)}</td><td>${esc(e.finding)}</td>
                 <td class="ref">${esc(e.reference ?? "")}</td></tr>`).join("");

  // Telemetry
  const t = dec.telemetry || {};
  document.getElementById("telemetry").innerHTML = `
    <span>Architecture <b>${esc(dec.architecture)}</b></span>
    <span>Latency <b>${esc(dec.latency_ms)} ms</b></span>
    <span>LLM calls <b>${esc(t.llm_calls ?? 0)}</b></span>
    <span>Tool calls <b>${esc(t.tool_calls ?? 0)}</b></span>
    <span>Tools: <b>${esc((t.tool_names || []).join(", ")) || "—"}</b></span>`;

  results.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

init();
