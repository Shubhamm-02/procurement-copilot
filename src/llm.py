"""Provider-agnostic LLM client with an automatic fallback chain.

Priority: Groq -> Anthropic (Claude) -> OpenAI. Whichever keys are present are
tried in that order; if the primary provider errors, the next available one is
used. If NO key is present (or every provider fails), callers fall back to a
deterministic, template-based narrative so the product still runs end-to-end and
the evaluation stays fully reproducible without any secret.

The LLM's job is strictly *interpretation and phrasing*. It is given the request,
the grounded tool evidence, and the deterministic engine's verdict, and asked to
explain — it cannot change approvals, risk flags, or missing-information, because
those come from policy_engine.py. Business text is passed as data, never as
instructions, and the system prompt says so explicitly.
"""
from __future__ import annotations

import json
import re

import httpx

from src import config


class LLMUnavailable(RuntimeError):
    """Raised when no provider can satisfy the request (caller uses fallback)."""


def _provider_chain() -> list[tuple[str, str, str]]:
    """(provider, api_key, model) for each configured provider, in priority order."""
    chain = []
    if config.GROQ_API_KEY:
        chain.append(("groq", config.GROQ_API_KEY, config.GROQ_MODEL))
    if config.ANTHROPIC_API_KEY:
        chain.append(("anthropic", config.ANTHROPIC_API_KEY, config.ANTHROPIC_MODEL))
    if config.OPENAI_API_KEY:
        chain.append(("openai", config.OPENAI_API_KEY, config.OPENAI_MODEL))
    return chain


def is_available() -> bool:
    return bool(_provider_chain())


def provider_label() -> str:
    chain = _provider_chain()
    if not chain:
        return "deterministic (no LLM key)"
    provider, _, model = chain[0]
    return f"{provider}:{model}"


def complete_json(system: str, user: str, max_tokens: int = 900) -> tuple[dict, str]:
    """Return (parsed_json, provider_used). Tries each provider until one works."""
    errors = []
    for provider, key, model in _provider_chain():
        try:
            if provider in ("groq", "openai"):
                text = _openai_compatible(provider, key, model, system, user, max_tokens)
            else:
                text = _anthropic(key, model, system, user, max_tokens)
            return _extract_json(text), f"{provider}:{model}"
        except Exception as exc:  # noqa: BLE001 — collect and try next provider
            errors.append(f"{provider}: {type(exc).__name__}: {exc}")
            continue
    raise LLMUnavailable("; ".join(errors) or "no LLM provider configured")


# ---------------------------------------------------------------------------
# provider implementations (REST via httpx — no SDKs)
# ---------------------------------------------------------------------------
def _openai_compatible(provider: str, key: str, model: str,
                       system: str, user: str, max_tokens: int) -> str:
    base = "https://api.groq.com/openai/v1" if provider == "groq" else "https://api.openai.com/v1"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    resp = httpx.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload, timeout=config.LLM_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _anthropic(key: str, model: str, system: str, user: str, max_tokens: int) -> str:
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json=payload, timeout=config.LLM_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    parts = resp.json().get("content", [])
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text")


def _extract_json(text: str) -> dict:
    """Parse a JSON object from a model response, tolerating code fences/prose."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise
