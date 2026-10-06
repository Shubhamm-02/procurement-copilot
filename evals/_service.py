"""Make the evaluation self-contained.

The copilot gathers vendor risk through the external Vendor Risk API (by design).
So the evaluation needs that service running. Rather than require the grader to
start it in a separate terminal (and silently fail PUB-01 if they forget), the
eval scripts use this helper to auto-start the mock API on :8001 when it isn't
already reachable, and shut it down afterwards.
"""
from __future__ import annotations

import contextlib
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
API_HEALTH = "http://127.0.0.1:8001/health"


def _reachable(url: str) -> bool:
    try:
        return requests.get(url, timeout=0.5).ok
    except requests.RequestException:
        return False


@contextlib.contextmanager
def ensure_vendor_risk_api(timeout: float = 15.0):
    """Ensure the Vendor Risk API is up for the duration of the block.

    If it is already running, use it and leave it alone. Otherwise start it as a
    subprocess and terminate it on exit.
    """
    if _reachable(API_HEALTH):
        print("Vendor Risk API already running on :8001 — using it.")
        yield False
        return

    print("Starting Vendor Risk API on :8001 for the evaluation ...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mock_api.app:app",
         "--host", "127.0.0.1", "--port", "8001"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("Vendor Risk API failed to start (is port 8001 busy?).")
            if _reachable(API_HEALTH):
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("Vendor Risk API did not become ready in time.")
        yield True
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
