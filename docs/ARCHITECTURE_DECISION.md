# Architecture Decision Memo

## Decision
**Ship the single agent (Architecture A).**

## Evidence
Same six public cases, run through both architectures (`python evals/compare_architectures.py`).

| Metric | Single agent (A) | Staged / 2-agent (B) |
|---|---:|---:|
| Cases passing minimum checks | **6/6** | 6/6 |
| Avg LLM calls / case | **1** (0 in no-key mode) | **2** (0 in no-key mode) |
| Avg tool calls / case | 7 | 7 |
| Avg latency / case | lower | higher (extra LLM round-trip) |
| Notable policy/grounding failures | none | none |

Both architectures produce **identical** `required_approvals`, `risk_flags`,
`missing_information`, and `human_review_required` on every case. That is by design:
those fields come from one deterministic policy engine that both architectures share,
not from the model. The numbers above are from deterministic (no-key) mode, which is
fully reproducible; with an LLM key the only metric that moves is **LLM calls per case
(1 vs 2)** and the latency/cost that follows — correctness does not change.

## Trade-offs
The staged variant adds a second LLM role (Analyst → Reviewer). In exchange for a
slightly richer written narrative, it **doubles LLM calls, latency, and token cost**
and adds a second point of failure, while the decision itself is unchanged. The extra
agent reasons over the *same* evidence and the *same* authoritative verdict, so it has
nothing correctness-relevant to add.

## Risks / limitations
- **Model variance:** a weaker model could phrase a recommendation imprecisely. Mitigated
  because approvals/flags are deterministic; wording cannot change the control outcome.
- **Policy drift:** thresholds and the reference date live in `config.py`/policy and are
  unit-tested, but must be updated when the real policy changes.
- **Dimension data quality:** the vendor registry can be stale; we surface registry-vs-API
  conflicts rather than silently trusting one source.
- Before production I would validate on the hidden set, add authn + audit logging, and
  pen-test the injection scanner against obfuscated payloads.

## Why this is the right MVP
The client problem is **gather evidence, apply policy, keep humans in control** — a
problem whose hard constraints (budget, thresholds, security/privacy/legal, expiry) are
deterministic. The right place for reliability is code, not a second model. The single
agent uses the LLM where it genuinely helps (interpreting the business need, writing a
clear recommendation) and leaves every sensitive decision to deterministic rules plus a
human. A second agent would add orchestration, latency, and cost without improving any
measured outcome. Per the brief's own guidance — *"a simpler system that performs as
well or better is a stronger answer than unnecessary orchestration"* — the single agent
is the system to ship, with the staged variant retained as a tested comparison baseline.

*(~360 words.)*
