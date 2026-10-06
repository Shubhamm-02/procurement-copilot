"""Central configuration — the single place that defines *where the truth lives*.

Two kinds of configuration:
  1. Policy constants (reference date, approval thresholds, expiry window). These
     are read from / aligned with data/procurement_policy.md so the deterministic
     engine and the policy document never drift apart.
  2. LLM provider settings (priority order, model names). The agent tries
     Groq -> Anthropic -> OpenAI -> a deterministic no-LLM fallback, so the
     product runs with whatever key is available (or none at all).
"""
from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

# Load .env if present; never override variables already set in the environment.
load_dotenv(ROOT / ".env", override=False)


# ---------------------------------------------------------------------------
# Policy constants (grounded in data/procurement_policy.md)
# ---------------------------------------------------------------------------
def _parse_reference_date() -> date:
    """Read the policy's data-snapshot date so date checks never depend on the
    computer clock (required by the assignment)."""
    text = (DATA_DIR / "procurement_policy.md").read_text(encoding="utf-8")
    m = re.search(r"reference date:\*\*\s*(\d{4})-(\d{2})-(\d{2})", text, re.IGNORECASE)
    if not m:
        return date(2026, 9, 30)  # documented fallback
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


REFERENCE_DATE: date = _parse_reference_date()

# Vendor security assessment is current for this many days (policy §5).
SECURITY_REVIEW_VALID_DAYS = 365

# Financial approval thresholds (policy §4). (upper_bound_inclusive, approvals).
# None upper bound = no ceiling.
APPROVAL_THRESHOLDS: list[tuple[float | None, list[str]]] = [
    (1_000.0, ["Manager"]),
    (10_000.0, ["Department Head", "Procurement"]),
    (25_000.0, ["Department Head", "Finance", "Procurement"]),
    (None, ["Department Head", "Finance", "CFO", "Procurement"]),
]

# New-vendor legal review kicks in at/above this annual spend (policy §7).
LEGAL_REVIEW_NEW_VENDOR_MIN_SPEND = 10_000.0

# Data-access levels that imply sensitive handling (policy §5, §6).
PII_DATA_LEVELS = {"employee_pii", "customer_pii"}
SECURITY_SENSITIVE_DATA_LEVELS = {
    "source_code", "confidential_documents", "credentials", "secrets",
    "production_telemetry", "customer_pii", "employee_pii",
}
# Integrations that imply production / cloud-account access (policy §5).
PRODUCTION_INTEGRATION_KEYWORDS = ("production", "cloud account", "cloud-account")


# ---------------------------------------------------------------------------
# LLM provider configuration (priority: Groq -> Anthropic -> OpenAI)
# ---------------------------------------------------------------------------
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))

VENDOR_RISK_BASE_URL = os.getenv("VENDOR_RISK_BASE_URL", "http://127.0.0.1:8001")


def active_provider() -> str:
    """Return the provider that will actually be used given the available keys."""
    if GROQ_API_KEY:
        return "groq"
    if ANTHROPIC_API_KEY:
        return "anthropic"
    if OPENAI_API_KEY:
        return "openai"
    return "deterministic"
