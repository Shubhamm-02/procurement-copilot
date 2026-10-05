"""Architecture A — single-agent baseline.

One agent: gather evidence with tools -> deterministic policy engine -> ONE LLM
reasoning call that interprets the need and phrases the recommendation. If no LLM
is configured (or it fails), the deterministic narrative from the engine is used,
so the path always completes.

Cost profile: 1 LLM call + N tool calls.
"""
from __future__ import annotations

from src import agent_base, llm
from src.contracts import ProcurementDecision

_SYSTEM = agent_base.SYSTEM_RULES + (
    "\n\nReturn ONLY a JSON object: "
    '{"need_summary": str, "recommendation": str, "next_step": str}. '
    "recommendation: one sentence naming the action a human should take and why. "
    "next_step: one concrete sentence (who reviews / what to request), consistent "
    "with required_approvals and risk_flags."
)


def run(request_id: str) -> ProcurementDecision:
    p = agent_base.prepare(request_id)
    recommendation, next_step, llm_calls = p.engine.recommendation, p.engine.next_step, 0
    extra_evidence = None

    if llm.is_available():
        try:
            result, _provider = llm.complete_json(_SYSTEM, agent_base.llm_context(p))
            llm_calls = 1
            recommendation = result.get("recommendation", recommendation)
            next_step = result.get("next_step", next_step)
            if result.get("need_summary"):
                extra_evidence = [{
                    "source": "agent_interpretation",
                    "finding": str(result["need_summary"])[:400],
                    "reference": "single-agent",
                }]
        except Exception:
            # Any provider failure -> keep the deterministic narrative (reliable).
            llm_calls = 0

    return agent_base.to_decision(p, recommendation, next_step, llm_calls, extra_evidence)
