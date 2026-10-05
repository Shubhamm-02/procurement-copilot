"""Deterministic policy engine — the authoritative, testable core.

Given a request and the tool-gathered evidence bundle, this module computes the
*sensitive* fields with pure code (no LLM): required approvals, risk flags,
missing information, and whether a human must review. The LLM layer may phrase
the recommendation, but it can never change these outputs — which is exactly the
"AI interprets · CODE checks · HUMAN approves" split the brief asks for, and why
the system stays correct even with a weak/absent model or injected business text.

Every rule cites the policy section it implements (see data/procurement_policy.md).
Date checks use config.REFERENCE_DATE (the policy snapshot), never the clock.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from src import config


# Canonical approval + risk vocabulary (aligned with contracts.py suggestions).
APPROVAL_ORDER = ["Manager", "Department Head", "Finance", "CFO", "Procurement",
                  "Security", "Privacy", "Legal"]


@dataclass
class EngineResult:
    required_approvals: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    human_review_required: bool = True
    rationale: dict = field(default_factory=dict)      # structured facts for narrative
    recommendation: str = ""                            # deterministic default
    next_step: str = ""                                 # deterministic default


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _days_since(d: date | None) -> int | None:
    if d is None:
        return None
    return (config.REFERENCE_DATE - d).days


def _assessment_state(review_date: str | None, status: str | None) -> str:
    """Classify a vendor security assessment as current / expired / not_completed."""
    status_n = (status or "").strip().lower()
    if status_n in ("not_completed", "unknown", "pending", ""):
        return "not_completed"
    days = _days_since(_parse_date(review_date))
    if status_n == "expired":
        return "expired"
    if days is None:
        return "not_completed"
    if days > config.SECURITY_REVIEW_VALID_DAYS:
        return "expired"
    if status_n == "approved":
        return "current"
    return "not_completed"


def evaluate(request: dict, evidence: dict) -> EngineResult:
    r = EngineResult()
    cost = request.get("annual_cost_usd")
    data_level = (request.get("data_access_level") or "").strip().lower()
    integrations = [str(i).lower() for i in (request.get("requested_integrations") or [])]
    emp = evidence["employee"]
    budget = evidence["budget"]
    overlap = evidence["overlap"]
    registry = evidence["vendor_registry"]
    injection = evidence["injection"]
    vendor_risk = evidence["vendor_risk"]
    vendor = request.get("vendor_name", "")

    # ---- Policy §1: required information --------------------------------------
    if not emp.get("found"):
        r.missing_information.append("valid requester / department (requester not found in directory)")
    if cost is None:
        r.missing_information.append("annual cost (or a reasonable annual estimate)")
    if request.get("user_count") in (None, "", 0):
        r.missing_information.append("number of users / licenses")
    if data_level in ("", "unknown", "unspecified"):
        r.missing_information.append("intended data-access level")
    if not (request.get("business_justification") or "").strip():
        r.missing_information.append("business purpose / justification")
    if r.missing_information:
        r.risk_flags.append("missing_information")

    # ---- Policy §9: untrusted content / prompt injection ---------------------
    if injection.get("injection_detected"):
        r.risk_flags.append("prompt_injection_detected")
        r.rationale["injection"] = (
            "Request/business text attempted to override procurement rules; the "
            "embedded instruction was ignored and real policy applied."
        )

    # ---- Policy §2: budget check --------------------------------------------
    if budget.get("within_budget") is False:
        r.risk_flags.append("budget_insufficient")
        _add_approval(r, "Finance")
        r.rationale["budget"] = (
            f"Annual cost ${cost:,.0f} exceeds {emp.get('department')} available "
            f"software budget ${budget.get('available_usd', 0):,.0f} "
            f"(shortfall ${budget.get('shortfall_usd', 0):,.0f})."
        )

    # ---- Policy §3: existing-tool overlap -----------------------------------
    if overlap.get("has_overlap"):
        r.risk_flags.append("existing_tool_overlap")
        names = ", ".join(m["product_name"] for m in overlap["matches"][:4])
        r.rationale["overlap"] = (
            f"Existing approved catalog tools may overlap ({overlap['overlap_type']}): {names}. "
            "Surface for review; not an automatic rejection."
        )

    # ---- Policy §4: financial approval thresholds ---------------------------
    if cost is not None:
        for upper, approvals in config.APPROVAL_THRESHOLDS:
            if upper is None or cost <= upper:
                for a in approvals:
                    _add_approval(r, a)
                r.rationale["threshold"] = (
                    f"Annual amount ${cost:,.0f} requires minimum approvals: "
                    f"{', '.join(approvals)} (policy §4)."
                )
                break
    else:
        r.rationale["threshold"] = "Annual amount unknown — approval tier cannot be set until cost is provided."

    # ---- Vendor evidence: registry vs external service, staleness, conflict --
    reg_state = _assessment_state(
        registry.get("security_review_date"), registry.get("security_status")
    ) if registry.get("found") else "not_completed"

    api_available = vendor_risk.get("available")
    api_state = None
    if api_available:
        d = vendor_risk["data"]
        api_state = _assessment_state(d.get("last_review_date"), d.get("security_review_status"))
    else:
        r.risk_flags.append("vendor_risk_unavailable")
        r.rationale["vendor_risk"] = (
            f"External vendor-risk service could not be reached for '{vendor}' "
            f"({vendor_risk.get('error')}). Favorable status was NOT assumed."
        )

    # Policy §5: registry and external service disagree -> surface conflict.
    if api_available and registry.get("found") and api_state != reg_state:
        if {"current"} & {api_state, reg_state} and {"expired", "not_completed"} & {api_state, reg_state}:
            r.risk_flags.append("conflicting_vendor_evidence")
            r.rationale["conflict"] = (
                f"Internal registry ({reg_state}) and vendor-risk service ({api_state}) "
                f"disagree on '{vendor}' security status — routed to Security/manual review."
            )

    assessment_ok = (api_state == "current") if api_available else False
    if not assessment_ok and api_state != "current":
        if api_state == "expired" or reg_state == "expired":
            r.risk_flags.append("vendor_review_expired")

    # ---- Policy §5: security review -----------------------------------------
    security_reasons = []
    if data_level in config.SECURITY_SENSITIVE_DATA_LEVELS:
        security_reasons.append(f"data access level '{data_level}'")
    if any(any(k in i for k in config.PRODUCTION_INTEGRATION_KEYWORDS) for i in integrations):
        security_reasons.append("production/cloud-account integration")
    if data_level == "production_telemetry":
        security_reasons.append("production telemetry access")
    if not assessment_ok:
        security_reasons.append("vendor security assessment missing/expired/unverified")
    if "conflicting_vendor_evidence" in r.risk_flags:
        security_reasons.append("conflicting vendor security evidence")
    if security_reasons:
        r.risk_flags.append("security_review_required")
        _add_approval(r, "Security")
        r.rationale["security"] = "Security review required: " + "; ".join(sorted(set(security_reasons))) + "."

    # ---- Policy §6: privacy review ------------------------------------------
    privacy_reasons = []
    if data_level in config.PII_DATA_LEVELS:
        privacy_reasons.append(f"processes {data_level}")
    if api_available and vendor_risk["data"].get("stores_data_outside_region") and \
            (data_level in config.PII_DATA_LEVELS or vendor_risk["data"].get("processes_personal_data")):
        privacy_reasons.append("vendor stores personal data outside operating region")
    if privacy_reasons:
        r.risk_flags.append("privacy_review_required")
        _add_approval(r, "Privacy")
        r.rationale["privacy"] = "Privacy review required: " + "; ".join(sorted(set(privacy_reasons))) + "."

    # ---- Policy §7: legal review -------------------------------------------
    legal_reasons = []
    is_new_vendor = registry.get("is_new_vendor", False)
    if is_new_vendor and cost is not None and cost >= config.LEGAL_REVIEW_NEW_VENDOR_MIN_SPEND:
        legal_reasons.append(f"new vendor with annual spend ${cost:,.0f} ≥ ${config.LEGAL_REVIEW_NEW_VENDOR_MIN_SPEND:,.0f}")
    legal_terms = (registry.get("legal_terms_status") or "").strip().lower()
    if registry.get("found") and legal_terms not in ("approved", "standard", ""):
        legal_reasons.append(f"legal terms not standard/approved ('{registry.get('legal_terms_status')}')")
    if legal_reasons:
        r.risk_flags.append("legal_review_required")
        _add_approval(r, "Legal")
        r.rationale["legal"] = "Legal review required: " + "; ".join(sorted(set(legal_reasons))) + "."

    # ---- Policy §11: human authority ---------------------------------------
    r.human_review_required = True  # copilot recommends only; a human always decides.

    # ---- Build grounded evidence list --------------------------------------
    r.evidence = _build_evidence(request, evidence, reg_state, api_state)

    # ---- Deterministic recommendation + next step (LLM may re-phrase) -------
    _set_default_narrative(r, request, emp)

    # de-duplicate flags while preserving order
    r.risk_flags = _dedupe(r.risk_flags)
    r.required_approvals = _sort_approvals(r.required_approvals)
    return r


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _add_approval(r: EngineResult, name: str) -> None:
    if name not in r.required_approvals:
        r.required_approvals.append(name)


def _sort_approvals(approvals: list[str]) -> list[str]:
    return sorted(set(approvals), key=lambda a: APPROVAL_ORDER.index(a) if a in APPROVAL_ORDER else 99)


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for i in items:
        if i not in seen:
            seen.add(i); out.append(i)
    return out


def _build_evidence(request: dict, ev: dict, reg_state: str, api_state: str | None) -> list[dict]:
    out: list[dict] = []
    emp = ev["employee"]
    if emp.get("found"):
        out.append({"source": "employee_directory",
                    "finding": f"Requester {emp.get('name')} in {emp.get('department')} (level {emp.get('level')}).",
                    "reference": emp.get("employee_id")})
    b = ev["budget"]
    if b.get("found"):
        if b.get("within_budget") is None:
            finding = f"{b['department']} available software budget ${b['available_usd']:,.0f}; request cost unknown."
        else:
            finding = (f"{b['department']} available budget ${b['available_usd']:,.0f} vs request "
                       f"${b['annual_cost_usd']:,.0f} → {'within budget' if b['within_budget'] else 'OVER budget'}.")
        out.append({"source": "budget_check", "finding": finding, "reference": "policy §2"})
    ov = ev["overlap"]
    if ov.get("has_overlap"):
        names = ", ".join(f"{m['product_name']} ({m['software_id']})" for m in ov["matches"][:4])
        out.append({"source": "software_catalog",
                    "finding": f"Possible overlap ({ov['overlap_type']}): {names}.",
                    "reference": "policy §3"})
    else:
        out.append({"source": "software_catalog",
                    "finding": "No overlapping approved tool found for this vendor/product/category.",
                    "reference": "policy §3"})
    reg = ev["vendor_registry"]
    if reg.get("found"):
        out.append({"source": "vendor_registry",
                    "finding": (f"Registry: procurement={reg['procurement_status']}, "
                                f"security={reg['security_status']} ({reg_state}), "
                                f"legal_terms={reg['legal_terms_status']}, new_vendor={reg['is_new_vendor']}."),
                    "reference": reg.get("vendor_name")})
    vr = ev["vendor_risk"]
    if vr.get("available"):
        d = vr["data"]
        out.append({"source": "vendor_risk_api",
                    "finding": (f"External risk={d.get('risk_level')}, security={d.get('security_review_status')} "
                                f"({api_state}), personal_data={d.get('processes_personal_data')}, "
                                f"outside_region={d.get('stores_data_outside_region')}."),
                    "reference": f"GET /vendor-risk/{request.get('vendor_name')}"})
    else:
        out.append({"source": "vendor_risk_api",
                    "finding": f"External vendor-risk service unavailable ({vr.get('error')}); status not verified.",
                    "reference": f"GET /vendor-risk/{request.get('vendor_name')} → {vr.get('status_code')}"})
    if ev["injection"].get("injection_detected"):
        out.append({"source": "injection_scan",
                    "finding": "Untrusted instruction detected in business text; ignored per policy §9.",
                    "reference": "policy §9"})
    return out


def _set_default_narrative(r: EngineResult, request: dict, emp: dict) -> None:
    product = request.get("product_name", "the request")
    if r.missing_information:
        r.recommendation = "Request clarification before review"
        r.next_step = ("Return to requester for: " + "; ".join(r.missing_information) +
                       ". Do not proceed until provided.")
        return
    reviews = [a for a in r.required_approvals if a in ("Security", "Privacy", "Legal")]
    if "budget_insufficient" in r.risk_flags:
        r.recommendation = f"Hold {product} for budget exception + required approvals"
        r.next_step = ("Route to Finance for a budget-exception decision, then "
                       + ", ".join(r.required_approvals) + " for sign-off.")
    elif reviews:
        r.recommendation = f"Proceed to human review for {product} (conditions apply)"
        r.next_step = ("Route to " + ", ".join(r.required_approvals) +
                       ". Clear " + ", ".join(reviews) + " review(s) before any purchase.")
    else:
        r.recommendation = f"Recommend approval of {product}, pending sign-off"
        r.next_step = "Route to " + ", ".join(r.required_approvals) + " for standard approval."
