#!/usr/bin/env python
"""Production smoke test against a deployed backend.

Usage:
    python scripts/smoke_test.py https://your-backend.example.com
    APP_TOKEN=... python scripts/smoke_test.py https://your-backend.example.com

Verifies the deployment end to end without touching provider integrations:
health, auth rejection, authenticated reads, project/task creation, task
execution to a terminal state, and cleanup. Exits non-zero on any failure.
"""

import argparse
import os
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load_env() -> None:
    from dotenv import load_dotenv

    here = Path(__file__).resolve().parents[1]
    for candidate in (here / ".env", here.parent / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", help="deployed backend base URL")
    parser.add_argument("--timeout", type=int, default=300, help="seconds to wait for task completion")
    parser.add_argument("--keep", action="store_true", help="keep the smoke-test project afterwards")
    args = parser.parse_args()

    _load_env()
    token = os.environ.get("APP_TOKEN")
    if not token:
        print("FAIL: APP_TOKEN must be set (env or backend/.env)")
        sys.exit(1)

    base = args.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    client = httpx.Client(base_url=base, timeout=30, headers=headers, follow_redirects=True)
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        status = "ok  " if condition else "FAIL"
        print(f"{status}  {name}" + (f" — {detail}" if detail else ""))
        if not condition:
            failures.append(name)

    # 1. Health is public.
    response = httpx.get(f"{base}/api/health", timeout=15)
    check("health endpoint", response.status_code == 200, f"HTTP {response.status_code}")

    # 2. Auth is enforced: no token, wrong token.
    check(
        "anonymous request rejected",
        httpx.get(f"{base}/api/projects", timeout=15).status_code == 401,
    )
    check(
        "bad token rejected",
        httpx.get(
            f"{base}/api/projects",
            headers={"Authorization": "Bearer wrong-token"},
            timeout=15,
        ).status_code
        == 401,
    )

    # 3. Authenticated reads work.
    response = client.get("/api/projects")
    check("authenticated projects list", response.status_code == 200, f"HTTP {response.status_code}")
    response = client.get("/api/settings")
    check("settings readable", response.status_code == 200, f"HTTP {response.status_code}")

    # 4. Create a project + chat task and wait for a terminal state.
    response = client.post("/api/projects", json={"kind": "chat", "name": "smoke-test"})
    if response.status_code != 201:
        check("project creation", False, f"HTTP {response.status_code} {response.text[:200]}")
        print(f"\n{len(failures)} check(s) failed")
        sys.exit(1)
    project_id = response.json()["id"]
    check("project creation", True)

    response = client.post(
        f"/api/projects/{project_id}/tasks",
        json={"prompt": "Reply with exactly: smoke test ok"},
    )
    if response.status_code != 201:
        check("task creation", False, f"HTTP {response.status_code} {response.text[:200]}")
        if not args.keep:
            client.delete(f"/api/projects/{project_id}")
        sys.exit(1)
    task_id = response.json()["id"]
    check("task creation", True)

    deadline = time.monotonic() + args.timeout
    terminal = {"done", "failed", "stopped"}
    status = ""
    while time.monotonic() < deadline:
        status = client.get(f"/api/tasks/{task_id}").json().get("status", "")
        if status in terminal:
            break
        time.sleep(3)
    check(
        "task reaches a terminal state",
        status in terminal,
        f"last status: {status or 'unknown'} (waited {args.timeout}s)",
    )

    # 5. Events were persisted for the run.
    events = client.get(f"/api/tasks/{task_id}/events")
    check(
        "task events persisted",
        events.status_code == 200 and len(events.json()) > 0,
    )

    if not args.keep:
        client.delete(f"/api/projects/{project_id}")

    print(f"\n{'ALL CHECKS PASSED' if not failures else f'{len(failures)} CHECK(S) FAILED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
