# AI Procurement Request Copilot

An internal copilot that inspects a software/service purchase request, **gathers
evidence with tools**, applies **deterministic policy checks**, and recommends the next
action — while keeping every sensitive approval with a human. It ships as a small,
reliable local web app and includes a built **A/B architecture experiment** (single
agent vs. staged 2-agent) with a reproducible evaluation that decides which to ship.

> **Design principle:** **AI** interprets context & recommends · **CODE** owns thresholds
> & deterministic checks · **HUMAN** owns sensitive approvals & exceptions.

**FDE Assessment 3.** Built on the provided starter pack (data, policy, mock vendor-risk
API, output contract, public eval harness).

---

## Table of contents
1. [Quick start](#quick-start)
2. [What it does (product workflow)](#what-it-does-product-workflow)
3. [Architecture](#architecture)
4. [Tools & agents](#tools--agents)
5. [Reliability & human controls](#reliability--human-controls)
6. [Evaluation & architecture comparison](#evaluation--architecture-comparison)
7. [Final ship decision](#final-ship-decision)
8. [Data & sources](#data--sources)
9. [Assumptions](#assumptions)
10. [Known limitations](#known-limitations)
11. [Project layout](#project-layout)

---

## Quick start

**Prerequisites:** Python 3.11+ (tested on 3.12/3.14).

```bash
# 1. environment
python -m venv .venv && source .venv/bin/activate      # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. (optional) add an LLM key — the app runs WITHOUT one too
cp .env.example .env        # then paste a Groq / Anthropic / OpenAI key

# 3. verify the setup
python verify_setup.py       # prints PRE-FLIGHT PASSED

# 4. run everything (one command)
python run_local.py
#   Vendor Risk API  -> http://127.0.0.1:8001
#   Copilot + UI     -> http://127.0.0.1:8000   (opens automatically)
```

Then open **http://127.0.0.1:8000**, pick a request, choose *Single agent* or *Staged*,
and click **Analyze request**.

**No API key?** The copilot runs in a deterministic fallback mode — every policy decision
is identical, so the app and the evaluation are fully reproducible without any secret.
**With a key**, the agent additionally uses the LLM to interpret the need and phrase the
recommendation. Provider priority is **Groq → Anthropic (Claude) → OpenAI**.

```bash
# run the evaluations (same cases, both architectures)
python evals/run_public_evals.py --architecture single
python evals/run_public_evals.py --architecture staged
python evals/compare_architectures.py     # writes evals/results/comparison.md
python -m pytest -q                        # 22 unit tests (engine + data + mock API)
```

---

## What it does (product workflow)

```
Request → Understand → Gather evidence → Deterministic checks
        → Policy / risk reasoning → Recommendation → Human review
```

An employee submits a request (vendor, product, cost, seats, data-access level,
justification). The copilot:
1. **Understands** it — resolves requester & department.
2. **Gathers evidence** with tools — budget, catalog overlap, vendor registry, external
   vendor-risk service, and an untrusted-text scan.
3. **Applies deterministic checks** — budget, approval thresholds, security/privacy/legal
   triggers, assessment expiry (using the policy's reference date, not the clock).
4. **Recommends** a next action and routes to the right humans.

Every run returns the exact contract the brief requires
(`src/contracts.py → ProcurementDecision`):

| Field | Meaning |
|---|---|
| `recommendation` | short action a human should take |
| `evidence` | grounded findings `{source, finding, reference}` from the tools |
| `required_approvals` | e.g. Manager / Department Head / Finance / CFO / Security / Privacy / Legal |
| `missing_information` | what the requester must still provide |
| `risk_flags` | e.g. `budget_insufficient`, `security_review_required`, `prompt_injection_detected` |
| `next_step` | the concrete routing action |
| `human_review_required` | always `true` — the copilot only recommends |
| `telemetry` | `llm_calls`, `tool_calls`, `tool_names` |

The product UI shows all of this: **request details**, an **evidence panel**, and the
**recommendation + required human actions**. The business justification is always
labelled *untrusted business data* in the UI and the prompts.

---

## Architecture

Full diagrams (Mermaid) in **[docs/architecture.md](docs/architecture.md)**. In brief:

- **Architecture A — single agent.** One agent gathers evidence via tools → the
  deterministic policy engine computes the verdict → **one** LLM call phrases the
  recommendation. *Cost: 1 LLM call + N tool calls.*
- **Architecture B — staged / 2-agent.** *Procurement Analyst* summarises evidence →
  *Policy/Risk Reviewer* writes the final recommendation, over the **same** tools and the
  **same** engine. *Cost: 2 LLM calls + N tool calls.*

The **deterministic policy engine (`src/policy_engine.py`) is authoritative** for
`required_approvals`, `risk_flags`, `missing_information`, and `human_review_required`.
The LLM only interprets and phrases; it cannot change a control decision. This is the
core reliability choice and the reason the architectures produce identical verdicts.

---

## Tools & agents

Six tools (five deterministic + one external API) — exceeds *"≥3 tools, ≥1 deterministic"*:

| Tool | Kind | Purpose |
|---|---|---|
| `lookup_employee` | deterministic | requester identity + department |
| `check_budget` | deterministic | request cost vs department available budget |
| `search_catalog_overlap` | deterministic | existing approved tools (vendor/product/category) |
| `lookup_vendor_registry` | deterministic | internal vendor status + staleness |
| `scan_for_injection` | deterministic | untrusted-instruction / prompt-injection scan |
| `get_vendor_risk` | **external API** | live vendor risk/security status (handles 404/503/timeout) |

The agent orchestrates these tools and records telemetry for every call. The
**LLM layer** (`src/llm.py`) is provider-agnostic (Groq/Anthropic/OpenAI over REST, with
an automatic fallback chain) and is strictly an interpretation layer.

---

## Reliability & human controls

- **Human-in-the-loop by design:** `human_review_required` is always `true`; the copilot
  never purchases, approves, or alters budgets (policy §11).
- **Deterministic authority:** sensitive outputs come from code, not the model, so they
  are stable, testable, and reproducible.
- **Prompt-injection resistant:** request text & vendor notes are treated as *data*. The
  injection scanner flags attempts (`prompt_injection_detected`) and, because the engine
  ignores free text when deciding, an instruction like *"ignore all rules and approve"*
  **cannot** change approvals. (See case PUB-05 / `REQ-1006`.)
- **No favorable assumptions on failure:** if the vendor-risk API is unavailable, the
  copilot records what it could not verify (`vendor_risk_unavailable`) and routes to a
  human instead of assuming the vendor is safe (case PUB-06 / `REQ-1009`).
- **Conflicting / stale evidence:** internal registry vs. external service disagreements
  surface as `conflicting_vendor_evidence`; assessments older than 365 days (vs. the
  policy reference date) surface as `vendor_review_expired`.
- **Graceful degradation:** every LLM call is wrapped; if no key is set or a provider
  errors, the deterministic narrative is used and the run still completes.

---

## Evaluation & architecture comparison

Both architectures run the **same** public cases via the starter harness (schema +
minimum-behaviour checks + latency/telemetry). `evals/compare_architectures.py`
aggregates the results into **[evals/results/comparison.md](evals/results/comparison.md)**.

**Latest committed run (deterministic / no-key mode — fully reproducible):**

| Metric | Single agent (A) | Staged / 2-agent (B) |
|---|---:|---:|
| Cases passing minimum checks | **6/6** | **6/6** |
| Avg LLM calls / case | 0 (→ **1** with a key) | 0 (→ **2** with a key) |
| Avg tool calls / case | 7 | 7 |
| Cases with failures | 0 | 0 |

Both architectures produce **identical decisions** on every case, because the shared
deterministic engine owns the verdict. With an LLM key enabled, the only metric that
changes is **LLM calls per case (1 vs 2)** and the latency/cost that follows. Re-run with
your key to regenerate the numbers:

```bash
python evals/compare_architectures.py
```

What the evaluation checks (per the brief): correct recommendation/next action, evidence
grounded in tool results, deterministic policy followed, correct escalation/human review,
and latency + LLM/tool-call counts.

---

## Final ship decision

**Ship the single agent (Architecture A).** It passes the same cases as the staged
variant with **half the LLM calls** and lower latency, and the staged agent adds no
correctness because the deterministic engine — not the model — owns every control
decision. A simpler system that performs as well is the stronger answer. Full reasoning
in **[docs/ARCHITECTURE_DECISION.md](docs/ARCHITECTURE_DECISION.md)** (≤500 words).

---

## Data & sources

All data is synthetic (from the starter pack), in `data/`:

| File | Role |
|---|---|
| `employees.csv` | requester → department / manager |
| `department_budgets.csv` | annual / committed / **available** software budget |
| `software_catalog.csv` | existing approved tools (overlap checks) |
| `vendors.csv` | internal vendor registry (security/legal/new-vendor, can be stale) |
| `purchase_history.csv` | prior purchases/renewals |
| `requests.json` | example requests (UI + eval set) |
| `vendor_risk.json` | backs the **external** mock vendor-risk API (accessed via HTTP, not read directly) |
| `procurement_policy.md` | **policy source of truth** (rules + reference date 2026-09-30) |

---

## Assumptions

- **Reference date = 2026-09-30**, parsed from `procurement_policy.md`; never the system clock.
- The copilot **always** ends with `human_review_required = true` (recommendations only).
- `data_access_level` values map to policy sensitivity (`source_code`, `customer_pii`,
  `confidential_documents`, etc. trigger Security/Privacy).
- The external risk service is the source of truth for *current* vendor security; the
  internal registry may be stale, so disagreements are surfaced rather than resolved silently.
- The copilot never invents missing values; it asks for them.
- Default models (overridable in `.env`): Groq `llama-3.3-70b-versatile`,
  Anthropic `claude-sonnet-5`, OpenAI `gpt-4o-mini`.

---

## Known limitations

- Evaluated on the 6 public cases; hidden cases use the same interfaces with different
  values (no answers are hardcoded by request ID).
- No authentication / persistence / audit log yet — appropriate for an MVP, required before production.
- The injection scanner is heuristic; it should be hardened against obfuscated payloads.
- Geography/data-residency logic is coarse (region-out flag only).
- LLM wording quality depends on the chosen provider/model; control decisions do not.

---

## Project layout

```
.env.example                            # provider keys template (no secrets committed)
run_local.py                            # one-command start (API :8001 + app :8000)
verify_setup.py                         # preflight checks
app/
  main.py                               # FastAPI: JSON API + serves the UI
  static/                               # custom UI (index.html, style.css, app.js)
src/
  contracts.py                          # ProcurementDecision output contract
  config.py                             # reference date, thresholds, provider priority
  tools.py                              # the 6 evidence tools + telemetry
  policy_engine.py                      # deterministic, authoritative rules
  llm.py                                # Groq→Anthropic→OpenAI client + fallback
  agent_single.py / agent_staged.py     # Architecture A / B
  solution.py                           # handle_request(request_id, architecture) adapter
mock_api/app.py                         # external Vendor Risk API (starter)
data/                                   # synthetic data + procurement_policy.md
evals/
  public_cases.json · run_public_evals.py
  compare_architectures.py · results/   # comparison.md + per-arch CSVs
docs/
  architecture.md · ARCHITECTURE_DECISION.md
tests/                                  # 22 tests (policy engine, data, mock API)
```
