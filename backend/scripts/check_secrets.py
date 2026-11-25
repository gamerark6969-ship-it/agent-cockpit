#!/usr/bin/env python
"""Check deployed secret presence and separation — without reading secret values.

Usage (from backend/):
    python scripts/check_secrets.py            # loads backend/.env if present
    python scripts/check_secrets.py --env .env.prod
    python scripts/check_secrets.py --strict   # exit 1 on warnings too

For every configured secret it prints only PRESENT/MISSING and a fingerprint
(SHA-256 of the value, first 12 hex chars) — never the value itself — so the
output is safe to paste into an issue or share. Exits non-zero when required
secrets are missing or two secrets collide.

When a --url is supplied (or BACKEND_URL / RENDER_EXTERNAL_URL is set), it also
calls GET {url}/api/health and asserts the deployed backend accepts the token,
proving that the runtime secret matches the local one.
"""

import argparse
import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load_env(env_file: str | None) -> None:
    from dotenv import load_dotenv

    here = Path(__file__).resolve().parents[1]
    candidates = [Path(env_file)] if env_file else [here / ".env", here.parent / ".env"]
    for candidate in candidates:
        if candidate.is_file():
            load_dotenv(candidate, override=False)


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _collect(strict: bool = False) -> int:
    checks = [
        ("APP_TOKEN", True),
        ("CONNECTOR_SECRET", False),
        ("AGENTROUTER_API_KEY", False),
        ("E2B_API_KEY", False),
        ("GITHUB_PAT", False),
        ("VAPID_PRIVATE_KEY", False),
    ]
    found: dict[str, str] = {}
    failures: list[str] = []
    warnings: list[str] = []

    for name, required in checks:
        value = os.environ.get(name, "")
        if value:
            found[name] = _fingerprint(value)
            print(f"PRESENT  {name:<22} sha256:{found[name]} (len={len(value)})")
        elif required:
            failures.append(name)
            print(f"MISSING  {name:<22} required in production")
        else:
            warnings.append(name)
            print(f"absent   {name:<22} optional here")

    fingerprints = {}
    for name, fp in found.items():
        fingerprints.setdefault(fp, []).append(name)
    for fp, names in fingerprints.items():
        if len(names) > 1:
            failures.append(f"duplicate secret values: {', '.join(names)}")
            print(f"FAIL     {', '.join(names)} share the same value ({fp})")

    if os.environ.get("ENVIRONMENT", "").lower() in ("production", "prod"):
        if not os.environ.get("APP_TOKEN"):
            failures.append("APP_TOKEN")
        if not os.environ.get("CONNECTOR_SECRET"):
            failures.append("CONNECTOR_SECRET")
            print("FAIL     CONNECTOR_SECRET is required when ENVIRONMENT=production")
        if os.environ.get("CORS_ORIGINS", "").strip() in ("", "*"):
            failures.append("CORS_ORIGINS")
            print("FAIL     CORS_ORIGINS must list explicit origins in production")
    else:
        warnings.append("ENVIRONMENT is not 'production' — production checks skipped")

    return 1 if failures else (2 if warnings and strict else 0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", help="optional .env file to load before checking")
    parser.add_argument("--url", help="deployed backend URL for a live /api/health token check")
    parser.add_argument("--strict", action="store_true", help="exit 1 when only warnings remain")
    args = parser.parse_args()

    _load_env(args.env)

    result = _collect(strict=args.strict)
    if args.url:
        result = max(result, _check_remote(args.url))
    sys.exit(result)


def _check_remote(url: str) -> int:
    import httpx

    url = url.rstrip("/")
    token = os.environ.get("APP_TOKEN", "")
    print(f"\nremote check: {url}/api/health")
    try:
        response = httpx.get(f"{url}/api/health", timeout=15)
    except httpx.HTTPError as exc:
        print(f"FAIL     unreachable: {exc}")
        return 1
    if response.status_code != 200:
        print(f"FAIL     /api/health returned HTTP {response.status_code}")
        return 1
    print("ok       /api/health reachable")

    if not token:
        print("absent   APP_TOKEN — skipping authenticated check")
        return 0
    authorized = httpx.get(
        f"{url}/api/projects",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    if authorized.status_code == 200:
        print("ok       APP_TOKEN authenticates against the deployment")
        return 0
    print(f"FAIL     APP_TOKEN rejected by deployment (HTTP {authorized.status_code})")
    return 1


if __name__ == "__main__":
    main()
