import re
from typing import Optional

from e2b import AsyncSandbox
from e2b.sandbox.commands.command_handle import CommandExitException

from .config import settings

MAX_OUTPUT_CHARS = 8000
REPO_DIR = "/home/user/repo"

_TOKEN_PATTERNS = [
    re.compile(r"x-access-token:[^@\s'\"]+"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
]


def mask_secrets(text: str) -> str:
    """Mask tokens in any command/output destined for events or logs."""
    if not text:
        return text
    pat = settings.GITHUB_PAT
    if pat and len(pat) > 4 and pat in text:
        text = text.replace(pat, "***")
    for rx in _TOKEN_PATTERNS:
        text = rx.sub("***", text)
    return text


class SandboxError(Exception):
    def __init__(self, message: str, exit_code: Optional[int] = None):
        super().__init__(message)
        self.exit_code = exit_code


class SandboxUnavailable(SandboxError):
    pass


def _require_key() -> str:
    key = settings.E2B_API_KEY
    if not key:
        raise SandboxUnavailable("E2B_API_KEY not configured")
    return key


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS] + "\n... [truncated]", True
    return text, False


def _sh(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _glob_to_regex(pattern: str) -> str:
    parts = []
    for ch in pattern:
        if ch == "*":
            parts.append(".*")
        elif ch == "?":
            parts.append(".")
        else:
            parts.append(re.escape(ch))
    return "".join(parts)


async def create_task_sandbox(task_id: str, repo_url: str, default_branch: str = "main") -> AsyncSandbox:
    """Create a sandbox, clone the repo (token never logged), set up git identity."""
    key = _require_key()
    envs = {"GIT_TERMINAL_PROMPT": "0"}
    pat = settings.GITHUB_PAT
    if pat:
        envs["GITHUB_PAT"] = pat
        envs["GITHUB_TOKEN"] = pat
    try:
        sbx = await AsyncSandbox.create(api_key=key, envs=envs, timeout=settings.SANDBOX_TIMEOUT_S)
    except Exception as exc:
        raise SandboxUnavailable(f"failed to create sandbox: {exc}") from exc
    try:
        # Token reaches git via an askpass helper that reads the env var; the token
        # itself never appears in a command string, event, or log.
        setup = "set -e\n"
        setup += "git config --global user.email agent@example.com\n"
        setup += "git config --global user.name 'AI Agent'\n"
        if pat:
            setup += "printf '#!/bin/sh\\necho \"$GITHUB_PAT\"\\n' > /usr/local/bin/git-token-askpass\n"
            setup += "chmod +x /usr/local/bin/git-token-askpass\n"
            setup += "git config --global core.askpass /usr/local/bin/git-token-askpass\n"
        clone_url = repo_url
        if pat and repo_url.startswith("https://"):
            clone_url = f"https://x-access-token:{pat}@{repo_url[len('https://'):]}"
        setup += f"mkdir -p {_sh(REPO_DIR.rsplit('/', 1)[0])}\n"
        # Filter clone output so a echoed token (if any) can never leak into logs.
        setup += (
            f"git clone {_sh(clone_url)} {_sh(REPO_DIR)} > /tmp/clone.log 2>&1; "
            "sed -e 's|x-access-token:[^@]*@|x-access-token:***@|g' -e 's|https://[^@]*@|https://***@|g' /tmp/clone.log\n"
        )
        setup += f"cd {_sh(REPO_DIR)}\n"
        setup += (
            f"git checkout {_sh(default_branch)} 2>/dev/null "
            f"|| git checkout -b {_sh(default_branch)} origin/{_sh(default_branch)} 2>/dev/null || true\n"
        )
        setup += f"git remote set-url origin {_sh(repo_url)}\n"
        try:
            result = await sbx.commands.run(setup, timeout=600)
        except CommandExitException as exc:
            out = ((exc.stdout or "") + "\n" + (exc.stderr or "")).strip()
            raise SandboxError(f"git clone/setup failed (exit {exc.exit_code}): {out[:800]}") from exc
        if result.exit_code != 0:
            out = (result.stdout or "") + (result.stderr or "")
            raise SandboxError(f"git clone failed (exit {result.exit_code}): {out[:500]}")
        return sbx
    except Exception as exc:
        try:
            await sbx.kill()
        except Exception:
            pass
        if isinstance(exc, SandboxError):
            raise
        raise SandboxError(f"failed to set up repo in sandbox: {exc}") from exc


async def connect(sandbox_id: str) -> AsyncSandbox:
    """Reconnect to an existing sandbox. Raises SandboxError if it is dead."""
    key = _require_key()
    try:
        sbx = await AsyncSandbox.connect(sandbox_id, api_key=key, timeout=settings.SANDBOX_TIMEOUT_S)
        result = await sbx.commands.run("echo ok", timeout=20)
        if result.exit_code != 0:
            raise SandboxError("sandbox health check failed")
        return sbx
    except SandboxError:
        raise
    except Exception as exc:
        raise SandboxError(f"sandbox {sandbox_id} unreachable: {exc}") from exc


async def keep_alive(sbx: AsyncSandbox, seconds: Optional[int] = None) -> None:
    """Extend the sandbox lifetime so long-running tasks are not killed mid-run."""
    try:
        await sbx.set_timeout(seconds or settings.SANDBOX_TIMEOUT_S)
    except Exception:
        pass


async def kill(sandbox_id: str) -> None:
    try:
        sbx = await AsyncSandbox.connect(sandbox_id, api_key=_require_key())
        await sbx.kill()
    except Exception:
        pass


async def run_command(sbx: AsyncSandbox, cmd: str, timeout: int = 300) -> dict:
    """Run a command. Returns {exit_code, output, truncated}; output is stdout+stderr combined."""
    try:
        result = await sbx.commands.run(cmd, timeout=timeout)
    except CommandExitException as exc:
        output = ((exc.stdout or "") + ("\n" + exc.stderr if exc.stderr else "")).strip()
        output, truncated = _truncate(output)
        return {"exit_code": exc.exit_code, "output": output, "truncated": truncated}
    except Exception as exc:
        return {
            "exit_code": -1,
            "output": f"sandbox command error: {exc}"[:MAX_OUTPUT_CHARS],
            "truncated": False,
        }
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    output = (stdout + ("\n" + stderr if stderr else "")).strip()
    output, truncated = _truncate(output)
    return {"exit_code": result.exit_code, "output": output, "truncated": truncated}


async def write_file(sbx: AsyncSandbox, path: str, content: str) -> dict:
    try:
        await sbx.files.write(path, content)
        return {"exit_code": 0, "output": f"wrote {path} ({len(content)} chars)", "truncated": False}
    except Exception as exc:
        return {"exit_code": -1, "output": f"sandbox write error: {exc}", "truncated": False}


async def read_file(sbx: AsyncSandbox, path: str) -> dict:
    try:
        content = await sbx.files.read(path)
        text = content if isinstance(content, str) else content.decode("utf-8", "replace")
        out, truncated = _truncate(text)
        return {"exit_code": 0, "output": out, "truncated": truncated}
    except Exception as exc:
        return {"exit_code": -1, "output": f"sandbox read error: {exc}", "truncated": False}


async def list_dir(sbx: AsyncSandbox, path: str) -> dict:
    return await run_command(sbx, f"ls -la --time-style=long-iso {_sh(path)} | head -100", timeout=60)


async def glob_files(sbx: AsyncSandbox, pattern: str, path: str = ".") -> dict:
    cmd = (
        f"find {_sh(path)} -type d \\( -name .git -o -name node_modules \\) -prune -o -type f -print 2>/dev/null"
        f" | grep -E {_sh(_glob_to_regex(pattern))} | head -200"
    )
    return await run_command(sbx, cmd, timeout=60)


async def grep_files(sbx: AsyncSandbox, pattern: str, path: str = ".") -> dict:
    cmd = (
        f"grep -rn -I -E {_sh(pattern)} {_sh(path)} --exclude-dir=.git --exclude-dir=node_modules 2>/dev/null | head -200"
    )
    return await run_command(sbx, cmd, timeout=120)
