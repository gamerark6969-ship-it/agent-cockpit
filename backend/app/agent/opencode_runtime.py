"""Run free models through OpenCode, with no filesystem or shell tools enabled."""

import asyncio
import json
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from ..config import settings


class FreeModelUnavailable(RuntimeError):
    """A safe, user-visible provider failure without raw provider logs."""


def parse_output(output: str) -> str:
    parts = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if event.get("type") == "error":
            raise RuntimeError("OpenCode free model request failed; try another free model")
        if event.get("type") == "text":
            text = (event.get("part") or {}).get("text", "")
            if isinstance(text, str):
                parts.append(text)
    text = "".join(parts).strip()
    if not text:
        raise RuntimeError("OpenCode returned no text")
    return text


def isolated_environment(root: Path) -> dict[str, str]:
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update({
        "HOME": str(root), "USERPROFILE": str(root),
        "XDG_CONFIG_HOME": str(root / "config"), "XDG_DATA_HOME": str(root / "data"),
        "XDG_CACHE_HOME": str(root / "cache"), "APPDATA": str(root / "config"),
        "LOCALAPPDATA": str(root / "data"), "OPENCODE_PURE": "1",
        "OPENCODE_DISABLE_PROJECT_CONFIG": "1", "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
        "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "1", "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
        "OPENCODE_CONFIG_CONTENT": json.dumps({"$schema": "https://opencode.ai/config.json",
            "permission": "deny", "share": "disabled", "autoupdate": False,
            "enabled_providers": ["opencode"], "plugin": [], "mcp": {}, "instructions": []}),
    })
    return env


def native_executable() -> str:
    executable = shutil.which(settings.OPENCODE_BIN)
    if not executable:
        raise RuntimeError("OpenCode is not installed")
    if os.name == "nt" and Path(executable).suffix.lower() in (".cmd", ".ps1"):
        native = Path(executable).parent / "node_modules/opencode-ai/bin/opencode.exe"
        if not native.is_file():
            raise RuntimeError("Set OPENCODE_BIN to the native opencode.exe")
        return str(native)
    return executable


async def run_free_model(model: str, prompt: str) -> str:
    from .models import FREE_MODELS

    if model not in FREE_MODELS or len(prompt) > 60000:
        raise ValueError("Unsupported free model or oversized prompt")
    executable = native_executable()
    with TemporaryDirectory(prefix="video-script-") as directory:
        root = Path(directory)
        process = await asyncio.create_subprocess_exec(
            executable, "run", "--pure", "--format", "json", "--model", model, "--print-logs", "--log-level", "ERROR",
            "Follow the task supplied on standard input. Return only the requested text; do not use tools.",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=root, env=isolated_environment(root),
        )
        async def read_stdout():
            output = bytearray()
            while chunk := await process.stdout.read(8192):
                output.extend(chunk)
                if len(output) > 1024 * 1024:
                    raise FreeModelUnavailable("OpenCode exceeded its response size limit")
            return bytes(output)

        async def check_errors():
            while line := await process.stderr.readline():
                if b"Free usage limit reached" in line:
                    raise FreeModelUnavailable("OpenCode free quota is exhausted; try again after it resets")
                if b"stream error" in line or b"ModelNotFound" in line:
                    raise FreeModelUnavailable("The selected OpenCode free model is unavailable")

        async def send_input():
            process.stdin.write(prompt.encode())
            await process.stdin.drain()
            process.stdin.close()

        tasks = [asyncio.create_task(read_stdout()), asyncio.create_task(check_errors()), asyncio.create_task(send_input())]
        try:
            try:
                output, _, _ = await asyncio.wait_for(asyncio.gather(*tasks), timeout=settings.OPENCODE_TIMEOUT_S)
                await process.wait()
            except TimeoutError:
                raise FreeModelUnavailable("OpenCode timed out; the free provider may be unavailable") from None
            if process.returncode or len(output) > 1024 * 1024:
                raise RuntimeError("OpenCode request failed or exceeded the output limit")
            return parse_output(output.decode("utf-8", errors="replace"))
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
