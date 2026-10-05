"""Assessment adapter — the single entry point the evaluation harness calls.

    handle_request(request_id, architecture) -> ProcurementDecision

Dispatches to Architecture A (single) or B (staged). Both return the same
ProcurementDecision contract, so the identical evaluation set runs on each.
"""
from __future__ import annotations

from src import agent_single, agent_staged
from src.contracts import Architecture, ProcurementDecision


def handle_request(request_id: str, architecture: Architecture = "single") -> ProcurementDecision:
    if architecture == "staged":
        return agent_staged.run(request_id)
    return agent_single.run(request_id)
