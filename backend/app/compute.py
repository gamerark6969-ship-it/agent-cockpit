"""Pluggable compute router.

Sandboxed work (shell, files, git, browser) runs on a compute provider. CPU is
always available via E2B cloud sandboxes. GPU is an optional provider left as a
no-op stub so the surface is stable and a backend can be plugged in later
without touching call sites.
"""

from typing import Optional

from . import sandbox as sbx_mod


class ComputeUnavailable(Exception):
    """Raised when a requested compute provider is not configured."""


PROVIDERS = ("cpu", "gpu")


def available_providers() -> list:
    return ["cpu"]


def resolve_provider(requested: Optional[str]) -> str:
    provider = (requested or "cpu").strip().lower()
    if provider in ("", "auto"):
        provider = "cpu"
    if provider not in PROVIDERS:
        raise ComputeUnavailable(f"unknown compute provider: {provider}")
    if provider == "gpu":
        raise ComputeUnavailable(
            "GPU provider is not configured. CPU (E2B) is the default; add a GPU "
            "backend to enable GPU tasks."
        )
    return provider


async def create_repo_sandbox(
    task_id: str,
    repo_url: str,
    default_branch: str = "main",
    provider: Optional[str] = None,
):
    resolve_provider(provider)
    return await sbx_mod.create_task_sandbox(task_id, repo_url, default_branch)


async def create_scratch_sandbox(task_id: str, provider: Optional[str] = None):
    resolve_provider(provider)
    return await sbx_mod.create_scratch_sandbox(task_id)
