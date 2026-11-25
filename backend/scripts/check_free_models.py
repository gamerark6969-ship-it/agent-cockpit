"""Check the real OpenCode runtime without loading personal keys or configuration."""

import argparse
import asyncio
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.opencode_runtime import isolated_environment, native_executable, run_free_model
from app.config import settings


async def check(model: str, timeout: int, catalog: bool, debug: bool = False) -> None:
    settings.OPENCODE_TIMEOUT_S = timeout
    if not catalog and not debug:
        result = await run_free_model(model, "Reply with exactly FREE_MODEL_OK and nothing else.")
        print("Free model response:", result)
        return
    with TemporaryDirectory(prefix="opencode-check-") as directory:
        process = await asyncio.create_subprocess_exec(
            native_executable(), *( ["models", "opencode", "--log-level", "ERROR"] if catalog else
                ["run", "--pure", "--format", "json", "--model", model, "--print-logs", "--log-level", "INFO"] ),
            cwd=directory, env=isolated_environment(Path(directory)),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            communication = asyncio.create_task(process.communicate(None if catalog else b"Reply with exactly FREE_MODEL_OK."))
            try:
                output, diagnostic = await asyncio.wait_for(asyncio.shield(communication), timeout=timeout)
            except TimeoutError:
                process.kill()
                output, diagnostic = await communication
            print("OpenCode check exit:", process.returncode)
            print(output.decode(errors="replace")[:8000])
            # The isolated process receives no tokens; diagnostics are limited to startup.
            print("Startup diagnostic:", diagnostic.decode(errors="replace")[-1800:])
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="opencode/big-pickle")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--catalog", action="store_true")
    parser.add_argument("--debug", action="store_true", help="Show credential-free startup diagnostics")
    args = parser.parse_args()
    try:
        asyncio.run(check(args.model, args.timeout, args.catalog, args.debug))
    except (RuntimeError, TimeoutError) as exc:
        print("Free-model check failed:", str(exc) or "startup timed out")
        raise SystemExit(1)
