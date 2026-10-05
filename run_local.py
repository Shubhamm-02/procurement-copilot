#!/usr/bin/env python3
"""One-command local start.

    python run_local.py

Starts two services and waits until both are healthy:
  - Vendor Risk API (external tool)  -> http://127.0.0.1:8001
  - Procurement Copilot app + UI     -> http://127.0.0.1:8000

Ctrl+C stops both. No API key is required to run — the copilot falls back to a
deterministic path if no LLM key is configured in .env.
"""
from __future__ import annotations

import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=False)

APP_URL = "http://127.0.0.1:8000"
API_URL = "http://127.0.0.1:8001"


def start(cmd: list[str]) -> subprocess.Popen:
    return subprocess.Popen(cmd, cwd=ROOT)


def wait_for(url: str, proc: subprocess.Popen, name: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"{name} exited during startup (code {proc.returncode}). "
                               "A busy port is the most common cause.")
        try:
            if requests.get(url, timeout=0.5).ok:
                return
        except requests.RequestException:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"{name} did not become ready within {timeout:.0f}s: {url}")


def _term(signum, frame):
    raise KeyboardInterrupt


def main() -> None:
    signal.signal(signal.SIGTERM, _term)
    procs: list[subprocess.Popen] = []
    try:
        print(f"Starting Vendor Risk API on {API_URL} ...")
        api = start([sys.executable, "-m", "uvicorn", "mock_api.app:app",
                     "--host", "127.0.0.1", "--port", "8001"])
        procs.append(api)
        wait_for(f"{API_URL}/health", api, "Vendor Risk API")
        print("  ready.")

        print(f"Starting Procurement Copilot on {APP_URL} ...")
        web = start([sys.executable, "-m", "uvicorn", "app.main:app",
                     "--host", "127.0.0.1", "--port", "8000"])
        procs.append(web)
        wait_for(f"{APP_URL}/api/health", web, "Copilot app")
        print("  ready.")

        print(f"\n  Open {APP_URL} in your browser.\n  Press Ctrl+C to stop.\n")
        try:
            webbrowser.open(APP_URL)
        except Exception:
            pass

        while True:
            time.sleep(1)
            for p in procs:
                if p.poll() is not None:
                    raise RuntimeError(f"A local process exited (code {p.returncode}).")
    except KeyboardInterrupt:
        print("\nStopping local services ...")
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
