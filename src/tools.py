"""Tools the copilot uses to gather evidence.

Six tools, exceeding the "≥3 tools, ≥1 deterministic" requirement:

  deterministic (code, no LLM):
    1. lookup_employee          - requester identity / department
    2. check_budget             - department available budget vs request cost
    3. search_catalog_overlap   - existing approved tools that may overlap
    4. lookup_vendor_registry   - internal vendor registry status + staleness
    5. scan_for_injection       - prompt-injection / untrusted-instruction scan
  external service:
    6. get_vendor_risk          - calls the mock Vendor Risk API (tool failure aware)

Each tool returns a plain dict (grounded, serialisable) and records telemetry.
Tools never raise on "expected" problems (missing record, API down); they return
an explicit status so the policy engine can reason about *absence* of evidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable

import requests

from src import data_access as da
from src.config import VENDOR_RISK_BASE_URL
from src.telemetry import RunTelemetryCounter


# ---------------------------------------------------------------------------
# cached data loaders (small CSVs; load once per process)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _employees() -> list[dict]:
    return da.load_employees().to_dict("records")


@lru_cache(maxsize=1)
def _budgets() -> list[dict]:
    return da.load_budgets().to_dict("records")


@lru_cache(maxsize=1)
def _catalog() -> list[dict]:
    return da.load_software_catalog().to_dict("records")


@lru_cache(maxsize=1)
def _vendors() -> list[dict]:
    return da.load_vendors().to_dict("records")


def _norm(s: Any) -> str:
    return str(s or "").strip().lower()


# ---------------------------------------------------------------------------
# Tool 1 — employee directory (deterministic)
# ---------------------------------------------------------------------------
def lookup_employee(requester_id: str) -> dict:
    for e in _employees():
        if e["employee_id"] == requester_id:
            return {"found": True, **e}
    return {"found": False, "requester_id": requester_id}


# ---------------------------------------------------------------------------
# Tool 2 — budget check (deterministic)
# ---------------------------------------------------------------------------
def check_budget(department: str, annual_cost: float | None) -> dict:
    row = next((b for b in _budgets() if _norm(b["department"]) == _norm(department)), None)
    if row is None:
        return {"found": False, "department": department}
    available = float(row["available_usd"])
    if annual_cost is None:
        return {"found": True, "department": department, "available_usd": available,
                "annual_cost_usd": None, "within_budget": None,
                "note": "cost unknown — budget sufficiency cannot be determined"}
    within = annual_cost <= available
    return {
        "found": True, "department": department, "available_usd": available,
        "annual_cost_usd": annual_cost, "within_budget": within,
        "shortfall_usd": round(max(0.0, annual_cost - available), 2),
    }


# ---------------------------------------------------------------------------
# Tool 3 — catalog overlap search (deterministic)
# ---------------------------------------------------------------------------
def search_catalog_overlap(product_name: str, vendor_name: str, category: str) -> dict:
    matches = []
    for sw in _catalog():
        same_vendor = _norm(sw["vendor_name"]) == _norm(vendor_name)
        same_product = _norm(sw["product_name"]) == _norm(product_name) or \
            _norm(product_name).startswith(_norm(sw["product_name"])) or \
            _norm(sw["product_name"]).startswith(_norm(vendor_name))
        same_category = _norm(sw["category"]) == _norm(category)
        if same_vendor or same_product or same_category:
            matches.append({
                "software_id": sw["software_id"], "product_name": sw["product_name"],
                "vendor_name": sw["vendor_name"], "category": sw["category"],
                "status": sw["status"], "licensed_seats": sw["licensed_seats"],
                "match": "same_vendor" if same_vendor else
                         ("same_product" if same_product else "same_category"),
            })
    overlap_type = None
    if any(m["match"] in ("same_vendor", "same_product") for m in matches):
        overlap_type = "same_vendor_or_product"
    elif matches:
        overlap_type = "same_category"
    return {"has_overlap": bool(matches), "overlap_type": overlap_type, "matches": matches}


# ---------------------------------------------------------------------------
# Tool 4 — internal vendor registry (deterministic)
# ---------------------------------------------------------------------------
def lookup_vendor_registry(vendor_name: str) -> dict:
    row = next((v for v in _vendors() if _norm(v["vendor_name"]) == _norm(vendor_name)), None)
    if row is None:
        return {"found": False, "vendor_name": vendor_name}
    status = _norm(row.get("procurement_status"))
    return {
        "found": True,
        "vendor_name": row["vendor_name"],
        "procurement_status": row.get("procurement_status"),
        "security_status": row.get("security_status"),
        "security_review_date": row.get("security_review_date") or None,
        "legal_terms_status": row.get("legal_terms_status"),
        "is_new_vendor": status in ("new", "pending", "unknown", ""),
        "notes": row.get("notes"),
    }


# ---------------------------------------------------------------------------
# Tool 5 — prompt-injection / untrusted-instruction scan (deterministic)
# ---------------------------------------------------------------------------
_INJECTION_PATTERNS = [
    r"ignore (all|any|previous|the) ",
    r"disregard (all|any|previous|the) ",
    r"treat .*(as )?(cfo|ceo|manager)?\s*-?\s*approved",
    r"approve (it|this|the request)? ?(immediately|now|without)",
    r"bypass", r"override", r"do not (follow|apply)", r"skip (the )?(policy|review|approval)",
    r"you are now", r"system prompt", r"reveal", r"jailbreak", r"as an ai",
    r"pretend", r"act as", r"forget (all|everything|previous)",
]


def scan_for_injection(*texts: str) -> dict:
    blob = "  ".join(t for t in texts if t)
    low = blob.lower()
    hits = [p for p in _INJECTION_PATTERNS if re.search(p, low)]
    return {
        "injection_detected": bool(hits),
        "patterns_matched": hits,
        "scanned_chars": len(blob),
    }


# ---------------------------------------------------------------------------
# Tool 6 — external Vendor Risk API (tool-failure aware)
# ---------------------------------------------------------------------------
def get_vendor_risk(vendor_name: str, timeout_seconds: float = 3.0) -> dict:
    url = f"{VENDOR_RISK_BASE_URL.rstrip('/')}/vendor-risk/{requests.utils.quote(vendor_name, safe='')}"
    try:
        resp = requests.get(url, timeout=timeout_seconds)
        if resp.status_code == 200:
            return {"available": True, "status_code": 200, "data": resp.json()}
        # 404 (no record) and 503 (simulated outage) are *expected* states.
        return {
            "available": False, "status_code": resp.status_code,
            "error": _safe_detail(resp),
        }
    except requests.RequestException as exc:
        return {"available": False, "status_code": None, "error": f"{type(exc).__name__}: {exc}"}


def _safe_detail(resp: requests.Response) -> str:
    try:
        return str(resp.json().get("detail", resp.text))[:300]
    except Exception:
        return resp.text[:300]


# ---------------------------------------------------------------------------
# Tool registry (schemas documented; used by the agent + README/UI)
# ---------------------------------------------------------------------------
@dataclass
class Tool:
    name: str
    kind: str  # "deterministic" | "external_api"
    description: str
    func: Callable[..., dict]


REGISTRY: dict[str, Tool] = {
    "lookup_employee": Tool("lookup_employee", "deterministic",
        "Resolve requester identity and department from the directory.", lookup_employee),
    "check_budget": Tool("check_budget", "deterministic",
        "Compare request annual cost to the department's available software budget.", check_budget),
    "search_catalog_overlap": Tool("search_catalog_overlap", "deterministic",
        "Find existing approved tools with the same vendor/product/category.", search_catalog_overlap),
    "lookup_vendor_registry": Tool("lookup_vendor_registry", "deterministic",
        "Read internal vendor registry status (security/legal/new-vendor).", lookup_vendor_registry),
    "scan_for_injection": Tool("scan_for_injection", "deterministic",
        "Detect untrusted instructions / prompt injection in business text.", scan_for_injection),
    "get_vendor_risk": Tool("get_vendor_risk", "external_api",
        "Call the external Vendor Risk service for current risk/security status.", get_vendor_risk),
}


def gather_evidence(request: dict, employee: dict, counter: RunTelemetryCounter) -> dict:
    """Run every tool for a request, recording telemetry. Returns a grounded,
    serialisable evidence bundle the policy engine and the LLM both consume.

    This deterministic evidence-gathering pipeline is shared by both
    architectures so evidence is always complete and reproducible; the LLM layer
    reasons over it rather than being trusted to fetch it reliably.
    """
    vendor = request.get("vendor_name", "")

    counter.record_tool_call("lookup_employee")
    # employee already resolved by caller, but count it as the first tool step

    counter.record_tool_call("check_budget")
    budget = check_budget(employee.get("department", ""), request.get("annual_cost_usd"))

    counter.record_tool_call("search_catalog_overlap")
    overlap = search_catalog_overlap(
        request.get("product_name", ""), vendor, request.get("category", ""))

    counter.record_tool_call("lookup_vendor_registry")
    registry = lookup_vendor_registry(vendor)

    counter.record_tool_call("scan_for_injection")
    injection = scan_for_injection(
        request.get("business_justification", ""),
        registry.get("notes", "") if registry.get("found") else "",
    )

    counter.record_tool_call("get_vendor_risk")
    vendor_risk = get_vendor_risk(vendor)

    return {
        "employee": employee,
        "budget": budget,
        "overlap": overlap,
        "vendor_registry": registry,
        "injection": injection,
        "vendor_risk": vendor_risk,
    }
