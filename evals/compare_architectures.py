#!/usr/bin/env python3
"""Reproducible architecture comparison.

Runs the SAME public evaluation set through Architecture A (single) and B (staged),
captures the minimum-behaviour pass result plus latency and LLM/tool-call counts,
and writes:

    evals/results/results_single.csv
    evals/results/results_staged.csv
    evals/results/comparison.md    (the table used in the decision memo / README)

Run:
    python evals/compare_architectures.py

Works with or without an LLM key. With no key, both architectures use the
deterministic path (llm_calls = 0) and should pass identically — which is itself
the headline finding. Add a Groq/Anthropic/OpenAI key to measure the real
single=1 vs staged=2 LLM-call / latency trade-off.
"""
from __future__ import annotations

import csv
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import llm  # noqa: E402
from src.contracts import ProcurementDecision  # noqa: E402
from src.solution import handle_request  # noqa: E402
from evals.run_public_evals import evaluate  # noqa: E402
from evals._service import ensure_vendor_risk_api  # noqa: E402

ARCHS = ["single", "staged"]
CASES = json.loads((ROOT / "evals" / "public_cases.json").read_text(encoding="utf-8"))
OUT_DIR = ROOT / "evals" / "results"


def run_arch(arch: str) -> list[dict]:
    rows = []
    for case in CASES:
        start = time.perf_counter()
        try:
            decision = handle_request(case["request_id"], architecture=arch)
            latency_ms = (time.perf_counter() - start) * 1000
            failures = evaluate(decision, case["expectations"])
            tel = decision.telemetry
            rows.append({
                "case_id": case["case_id"], "request_id": case["request_id"],
                "title": case["title"], "architecture": arch,
                "passed": not failures, "latency_ms": round(latency_ms, 1),
                "llm_calls": tel.llm_calls if tel else 0,
                "tool_calls": tel.tool_calls if tel else 0,
                "failures": " | ".join(failures),
            })
        except Exception as exc:  # noqa: BLE001
            latency_ms = (time.perf_counter() - start) * 1000
            rows.append({
                "case_id": case["case_id"], "request_id": case["request_id"],
                "title": case["title"], "architecture": arch,
                "passed": False, "latency_ms": round(latency_ms, 1),
                "llm_calls": 0, "tool_calls": 0,
                "failures": f"ERROR: {type(exc).__name__}: {exc}",
            })
    return rows


def aggregate(rows: list[dict]) -> dict:
    return {
        "cases": len(rows),
        "passed": sum(1 for r in rows if r["passed"]),
        "avg_latency_ms": round(statistics.mean(r["latency_ms"] for r in rows), 1),
        "avg_llm_calls": round(statistics.mean(r["llm_calls"] for r in rows), 2),
        "avg_tool_calls": round(statistics.mean(r["tool_calls"] for r in rows), 2),
        "failures": sum(1 for r in rows if r["failures"]),
    }


def write_csv(rows: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    provider = llm.provider_label()
    print(f"\nArchitecture comparison on {len(CASES)} public cases | LLM: {provider}\n")

    results, aggs = {}, {}
    # Auto-start the Vendor Risk API if needed, so the comparison is self-contained.
    with ensure_vendor_risk_api():
        for arch in ARCHS:
            rows = run_arch(arch)
            results[arch] = rows
            aggs[arch] = aggregate(rows)
            write_csv(rows, OUT_DIR / f"results_{arch}.csv")
            a = aggs[arch]
            print(f"  {arch:7s}: {a['passed']}/{a['cases']} passed | "
                  f"avg {a['avg_latency_ms']} ms | {a['avg_llm_calls']} LLM | {a['avg_tool_calls']} tools")

    _write_comparison_md(provider, aggs, results)
    print(f"\nWrote {OUT_DIR.relative_to(ROOT)}/comparison.md and per-architecture CSVs.")


def _write_comparison_md(provider: str, aggs: dict, results: dict) -> None:
    s, st = aggs["single"], aggs["staged"]
    lines = [
        "# Architecture comparison — public evaluation set",
        "",
        f"- **LLM provider used for this run:** `{provider}`",
        f"- **Cases:** {s['cases']} (identical set for both architectures)",
        "",
        "| Metric | Single agent (A) | Staged / 2-agent (B) |",
        "|---|---:|---:|",
        f"| Cases passing minimum checks | {s['passed']}/{s['cases']} | {st['passed']}/{st['cases']} |",
        f"| Avg latency (ms) | {s['avg_latency_ms']} | {st['avg_latency_ms']} |",
        f"| Avg LLM calls / case | {s['avg_llm_calls']} | {st['avg_llm_calls']} |",
        f"| Avg tool calls / case | {s['avg_tool_calls']} | {st['avg_tool_calls']} |",
        f"| Cases with failures | {s['failures']} | {st['failures']} |",
        "",
        "## Per-case detail",
        "",
        "| Case | Title | A pass | A ms | A LLM | B pass | B ms | B LLM |",
        "|---|---|:--:|--:|--:|:--:|--:|--:|",
    ]
    by_case = {r["case_id"]: r for r in results["single"]}
    for r in results["staged"]:
        a = by_case[r["case_id"]]
        lines.append(
            f"| {r['case_id']} | {r['title']} | {'✓' if a['passed'] else '✗'} | "
            f"{a['latency_ms']} | {a['llm_calls']} | {'✓' if r['passed'] else '✗'} | "
            f"{r['latency_ms']} | {r['llm_calls']} |"
        )
    (OUT_DIR / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
