"""Architecture B — staged / two-agent variant.

Two LLM roles over the SAME tools + deterministic engine:
  1. Procurement Analyst  : reads evidence, summarises the real business need,
                            assesses overlap, lists open concerns.
  2. Policy / Risk Reviewer: takes the analyst's note + the engine verdict and
                            produces the final recommendation + next step.

This mirrors the brief's "staged" diagram. It is intentionally a faithful,
*lightweight* 2-agent pipeline so the evaluation can measure what the extra
agent actually buys (correctness vs. latency / LLM-call cost).

Cost profile: 2 LLM calls + N tool calls.
"""
from __future__ import annotations

import json

from src import agent_base, llm
from src.contracts import ProcurementDecision

_ANALYST_SYSTEM = agent_base.SYSTEM_RULES + (
    "\n\nROLE: Procurement Analyst. Summarise the evidence for a reviewer. "
    "Return ONLY JSON: "
    '{"need_summary": str, "overlap_assessment": str, "open_concerns": [str]}. '
    "Do not decide approvals; just analyse."
)

_REVIEWER_SYSTEM = agent_base.SYSTEM_RULES + (
    "\n\nROLE: Policy / Risk Reviewer. You receive the analyst note and the "
    "authoritative engine verdict. Return ONLY JSON: "
    '{"recommendation": str, "next_step": str}. '
    "recommendation: one sentence action for a human. next_step: one concrete "
    "sentence consistent with required_approvals and risk_flags."
)


def run(request_id: str) -> ProcurementDecision:
    p = agent_base.prepare(request_id)
    recommendation, next_step, llm_calls = p.engine.recommendation, p.engine.next_step, 0
    extra_evidence = None

    if llm.is_available():
        try:
            context = agent_base.llm_context(p)
            # Stage 1 — Analyst
            analysis, _ = llm.complete_json(_ANALYST_SYSTEM, context)
            llm_calls += 1
            # Stage 2 — Reviewer (sees analyst note + same authoritative context)
            reviewer_input = (
                context + "\n\nANALYST_NOTE:\n" + json.dumps(analysis, indent=2)
            )
            decision, _ = llm.complete_json(_REVIEWER_SYSTEM, reviewer_input)
            llm_calls += 1

            recommendation = decision.get("recommendation", recommendation)
            next_step = decision.get("next_step", next_step)
            if analysis.get("need_summary"):
                extra_evidence = [{
                    "source": "analyst_interpretation",
                    "finding": str(analysis["need_summary"])[:400],
                    "reference": "staged:analyst",
                }]
        except Exception:
            llm_calls = llm_calls  # keep whatever completed; fall back on narrative

    return agent_base.to_decision(p, recommendation, next_step, llm_calls, extra_evidence)
