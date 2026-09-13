import base64
import html as html_mod
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy import func, select

from ..db import SessionLocal
from ..events import append_event
from ..models import Artifact, Message, new_id
from .. import sandbox as sbx_mod
from .. import compute
from ..sandbox import REPO_DIR, WORK_DIR, SandboxError, mask_secrets

MAX_WEBFETCH_CHARS = 20000

# ── Tool definitions (OpenAI function schemas) ───────────

TOOL_DEFINITIONS: List[dict] = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command in the Linux sandbox. The working directory is your current workspace (run `pwd` to see it). Returns combined stdout+stderr and the exit code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to run"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default 300, max per settings)"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file from the sandbox.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Absolute or repo-relative path"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a text file in the sandbox.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace exactly one occurrence of old_string with new_string in a file. Fails if old_string is not found or appears more than once.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string", "description": "Exact text to replace (include surrounding lines for uniqueness)"},
                    "new_string": {"type": "string"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List the contents of a directory.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Directory path (default '.')"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files matching a glob pattern (e.g. '**/*.py'). Excludes .git and node_modules.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "Base path (default '.')" },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents with an extended regex. Excludes .git and node_modules.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "Base path (default '.')"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "repo_map",
            "description": "Get a compact map of a large repository: file count, language breakdown, directory tree (depth 2), the largest source files by line count, and the README head. Call this first on unfamiliar or big repos instead of listing/reading everything.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Repo root to map (default: your working directory)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deploy_site",
            "description": "Publish a static HTML page or folder from the sandbox to a public URL the user can open on any device. Use this instead of preview_file for HTML pages/sites. Returns a live URL and, when possible, a persistent URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File or directory to publish (default: index.html in your working directory)"},
                    "title": {"type": "string", "description": "Optional human-friendly title"},
                    "port": {"type": "integer", "description": "Existing app port to expose instead of serving static files (for fullstack apps already running)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "Stage all changes (git add -A) and commit them on the current branch.",
            "parameters": {
                "type": "object",
                "properties": {"message": {"type": "string", "description": "Commit message"}},
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_push",
            "description": "Push the current branch to origin (sets upstream).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_pr",
            "description": "Open a pull request on GitHub for the current branch using the gh CLI. Push first with git_push. Returns the PR URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string", "description": "PR description: what changed and how it was tested"},
                },
                "required": ["title", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch a URL as plain text (HTML is stripped). Max ~20k chars. Use for docs and APIs.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_open",
            "description": "Open a URL in a headless Chromium browser inside the sandbox. Returns the visible page text.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "Click an element in the browser (CSS selector). Navigations are tracked.",
            "parameters": {
                "type": "object",
                "properties": {"selector": {"type": "string"}},
                "required": ["selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_type",
            "description": "Type text into a form field in the browser (CSS selector).",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["selector", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_screenshot",
            "description": "Take a screenshot of the current page. Saved as an artifact viewable by the user.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_read_page",
            "description": "Read the visible text of the current browser page.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "preview_file",
            "description": "Publish a file you created in the sandbox so the user can preview it inline in the app. Use this for any HTML page, image, PDF, document, or source/text file the user should see. HTML renders live. Call this before finishing whenever you built or changed a file the user would want to see.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path, relative to your working directory or absolute"},
                    "title": {"type": "string", "description": "Optional human-friendly title to show in the app"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Signal that the task is complete. Provide a concise result summary (mention the PR URL if created).",
            "parameters": {
                "type": "object",
                "properties": {
                    "result_summary": {"type": "string", "description": "Summary of what was accomplished"},
                },
                "required": ["result_summary"],
            },
        },
    },
]


def all_tool_definitions() -> List[dict]:
    from .connector_tools import CONNECTOR_TOOL_DEFINITIONS

    return TOOL_DEFINITIONS + CONNECTOR_TOOL_DEFINITIONS


@dataclass
class ToolContext:
    task_id: str
    sandbox: Any
    settings_data: dict
    command_timeout_s: int = 600
    default_branch: str = "main"
    kind: str = "repo"
    repo_dir: str = REPO_DIR
    extras: Dict[str, Any] = field(default_factory=dict)

    async def ensure_sandbox(self):
        """Chat tasks start without a sandbox; create a scratch one on first sandbox tool use."""
        if self.sandbox is not None:
            return self.sandbox
        if self.kind != "chat":
            raise SandboxError("no sandbox is configured for this task")
        await _emit(
            self.task_id,
            "terminal",
            {
                "command": "creating ephemeral Linux sandbox",
                "exit_code": 0,
                "output": "spinning up a sandbox for code/command execution (first use in this chat)...",
                "truncated": False,
            },
        )
        self.sandbox = await compute.create_scratch_sandbox(self.task_id)
        self.repo_dir = WORK_DIR
        return self.sandbox


# ── helpers ──────────────────────────────────────────────


async def _emit(task_id: str, type_: str, payload: dict) -> None:
    async with SessionLocal() as session:
        await append_event(session, task_id, type_, payload)


async def _emit_terminal(ctx: ToolContext, command: str, result: dict) -> None:
    await _emit(
        ctx.task_id,
        "terminal",
        {
            "command": mask_secrets(command),
            "exit_code": result.get("exit_code", -1),
            "output": mask_secrets(result.get("output", "")),
            "truncated": bool(result.get("truncated", False)),
        },
    )


def _repo_path(path: str, ctx: "ToolContext") -> str:
    base = ctx.repo_dir or REPO_DIR
    if not path:
        return base
    if path.startswith("/") or path.startswith("~"):
        return path
    return f"{base}/{path}"


MAX_PREVIEW_BYTES = 8 * 1024 * 1024

_MIME_BY_EXT = {
    ".html": "text/html",
    ".htm": "text/html",
    ".css": "text/css",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
}


def guess_mime(filename: str) -> str:
    name = (filename or "").lower()
    if "." in name:
        ext = name[name.rfind(".") :]
        mime = _MIME_BY_EXT.get(ext)
        if mime:
            return mime
    return "application/octet-stream"


_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>")


def strip_html(text: str) -> str:
    text = _SCRIPT_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = html_mod.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


_REPO_MAP_SCRIPT = r'''
cd __BASE__ || exit 1
echo "## overview"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "tracked files: $(git ls-files | wc -l)"
  echo "tracked lines: $(git ls-files -z | xargs -0 wc -l 2>/dev/null | tail -n 1 | awk '{print $1}')"
else
  echo "files: $(find . -type f | wc -l)"
fi
echo
echo "## top level"
ls -1p | head -60
echo
echo "## directories (depth 2)"
find . -maxdepth 2 -type d \( -name .git -o -name node_modules -o -name .venv -o -name venv -o -name __pycache__ -o -name dist -o -name build -o -name .next \) -prune -o -type d -print 2>/dev/null | sed 's|^\./||' | sort | head -80
echo
echo "## largest files (lines)"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git ls-files -z | xargs -0 wc -l 2>/dev/null | grep -v ' total$' | sort -rn | head -50
else
  find . -type d \( -name .git -o -name node_modules -o -name .venv -o -name dist -o -name build \) -prune -o -type f -print0 2>/dev/null | xargs -0 wc -l 2>/dev/null | grep -v ' total$' | sort -rn | head -50
fi
echo
echo "## file types"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git ls-files | awk -F. 'NF>1{print $NF}' | sort | uniq -c | sort -rn | head -20
fi
echo
echo "## readme"
for f in README* readme*; do [ -f "$f" ] && { echo "--- $f"; head -50 "$f"; break; }; done
echo
echo "## manifests"
for f in package.json pyproject.toml requirements.txt go.mod Cargo.toml pom.xml; do
  [ -f "$f" ] && { echo "--- $f"; head -40 "$f"; }
done
exit 0
'''


def _repo_map_command(base: str) -> str:
    return _REPO_MAP_SCRIPT.replace("__BASE__", sbx_mod._sh(base))



# ── browser support ──────────────────────────────────────

BROWSER_SCRIPT = r'''
import json, sys

def main():
    action = sys.argv[1]
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    state_file = "/tmp/browser_state.json"
    state = {}
    try:
        with open(state_file) as f:
            state = json.load(f)
    except Exception:
        pass
    out = {"ok": True, "url": state.get("url"), "text": None, "screenshot": None, "error": None}
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            try:
                if action == "open":
                    page.goto(args["url"], wait_until="domcontentloaded", timeout=45000)
                elif state.get("url"):
                    page.goto(state["url"], wait_until="domcontentloaded", timeout=45000)
                else:
                    raise RuntimeError("no page open; call browser_open first")
                if action == "click":
                    page.click(args["selector"], timeout=15000)
                    page.wait_for_load_state("domcontentloaded", timeout=30000)
                elif action == "type":
                    page.fill(args["selector"], args["text"], timeout=15000)
                if action in ("open", "click", "read_page"):
                    out["text"] = (page.inner_text("body") or "")[:15000]
                out["url"] = page.url
                if action == "screenshot":
                    page.screenshot(path="/tmp/screenshot.png")
                    out["screenshot"] = "/tmp/screenshot.png"
                state["url"] = page.url
                with open(state_file, "w") as f:
                    json.dump(state, f)
            finally:
                browser.close()
    except Exception as e:
        out["ok"] = False
        out["error"] = str(e)
    print(json.dumps(out))

main()
'''


async def _ensure_playwright(ctx: ToolContext) -> Optional[str]:
    """Lazy-install playwright + chromium on first browser use. Returns error or None."""
    sbx = await ctx.ensure_sandbox()
    probe = await sbx_mod.run_command(sbx, "python -c 'import playwright' 2>/dev/null", timeout=60)
    if probe["exit_code"] == 0:
        return None
    install_cmd = (
        "pip install --quiet playwright && "
        "(python -m playwright install chromium --only-shell || python -m playwright install chromium --with-deps)"
    )
    await _emit(
        ctx.task_id,
        "terminal",
        {
            "command": "pip install playwright && python -m playwright install chromium --with-deps",
            "exit_code": 0,
            "output": "installing headless Chromium (first browser use only, may take a few minutes)...",
            "truncated": False,
        },
    )
    result = await sbx_mod.run_command(sbx, install_cmd, timeout=900)
    if result["exit_code"] != 0:
        return f"playwright install failed: {result['output'][:2000]}"
    return None


async def _run_browser(ctx: ToolContext, action: str, args: dict) -> Tuple[bool, str, Optional[dict]]:
    err = await _ensure_playwright(ctx)
    if err:
        return False, err, None
    sbx = await ctx.ensure_sandbox()
    await sbx_mod.write_file(sbx, "/tmp/agent_browser.py", BROWSER_SCRIPT)
    args_json = json.dumps(args)
    import shlex

    cmd = f"python /tmp/agent_browser.py {shlex.quote(action)} {shlex.quote(args_json)} 2>&1"
    result = await sbx_mod.run_command(sbx, cmd, timeout=180)
    await _emit_terminal(ctx, f"browser_{action}({args_json})", result)
    if result["exit_code"] != 0:
        return False, result["output"], None
    try:
        out = json.loads(result["output"])
    except Exception:
        return False, f"browser script returned invalid output: {result['output'][:500]}", None
    if not out.get("ok"):
        return False, f"browser error: {out.get('error')}", None
    text = out.get("text") or ""
    summary = f"url: {out.get('url')}\n{text[:4000]}"
    return True, summary, out


async def _read_screenshot_bytes(ctx: ToolContext) -> Optional[bytes]:
    sbx = await ctx.ensure_sandbox()
    try:
        data = await sbx.files.read("/tmp/screenshot.png", format="bytes")
        if isinstance(data, bytes):
            return data
    except Exception:
        pass
    try:
        result = await sbx.commands.run("base64 -w0 /tmp/screenshot.png", timeout=60)
        if result.exit_code == 0 and result.stdout:
            return base64.b64decode(result.stdout.strip())
    except Exception:
        pass
    return None


# ── dispatch ─────────────────────────────────────────────


async def dispatch(ctx: ToolContext, tool: str, args: dict) -> Tuple[bool, str, Optional[dict]]:
    """Execute a tool call. Returns (ok, summary, extra_data). Appends terminal/screenshot events."""
    try:
        return await _dispatch(ctx, tool, args)
    except SandboxError as exc:
        return False, f"sandbox error: {exc}", None
    except Exception as exc:
        return False, f"tool internal error: {type(exc).__name__}: {exc}", None


async def _dispatch(ctx: ToolContext, tool: str, args: dict) -> Tuple[bool, str, Optional[dict]]:
    sbx = ctx.sandbox

    if tool == "bash":
        sbx = await ctx.ensure_sandbox()
        command = str(args.get("command", ""))
        timeout = int(args.get("timeout") or 300)
        timeout = max(5, min(timeout, ctx.command_timeout_s))
        result = await sbx_mod.run_command(sbx, f"cd {ctx.repo_dir} && {command}", timeout=timeout)
        await _emit_terminal(ctx, command, result)
        ok = result["exit_code"] == 0
        summary = result["output"] or f"(no output, exit code {result['exit_code']})"
        return ok, summary, None

    if tool == "read_file":
        sbx = await ctx.ensure_sandbox()
        path = _repo_path(str(args.get("path", "")), ctx)
        result = await sbx_mod.read_file(sbx, path)
        await _emit_terminal(ctx, f"cat {path}", result)
        if result["exit_code"] != 0:
            return False, result["output"], None
        return True, result["output"], None

    if tool == "write_file":
        sbx = await ctx.ensure_sandbox()
        path = _repo_path(str(args.get("path", "")), ctx)
        content = str(args.get("content", ""))
        result = await sbx_mod.write_file(sbx, path, content)
        return result["exit_code"] == 0, result["output"], None

    if tool == "edit_file":
        sbx = await ctx.ensure_sandbox()
        path = _repo_path(str(args.get("path", "")), ctx)
        old_string = str(args.get("old_string", ""))
        new_string = str(args.get("new_string", ""))
        if not old_string:
            return False, "old_string must not be empty", None
        read = await sbx_mod.read_file(sbx, path)
        if read["exit_code"] != 0:
            return False, f"cannot read {path}: {read['output']}", None
        content = read["output"] if not read["truncated"] else None
        if content is None:
            return False, f"file {path} is too large to edit safely", None
        count = content.count(old_string)
        if count == 0:
            return False, f"old_string not found in {path}", None
        if count > 1:
            return False, f"old_string is ambiguous: {count} occurrences in {path}; add more context lines", None
        new_content = content.replace(old_string, new_string, 1)
        result = await sbx_mod.write_file(sbx, path, new_content)
        if result["exit_code"] != 0:
            return False, result["output"], None
        return True, f"edited {path}: replaced {len(old_string)} chars with {len(new_string)} chars", None

    if tool == "list_dir":
        sbx = await ctx.ensure_sandbox()
        path = _repo_path(str(args.get("path", ".")), ctx)
        result = await sbx_mod.list_dir(sbx, path)
        await _emit_terminal(ctx, f"ls -la {path}", result)
        return result["exit_code"] == 0, result["output"], None

    if tool == "glob":
        sbx = await ctx.ensure_sandbox()
        pattern = str(args.get("pattern", "*"))
        path = _repo_path(str(args.get("path", ".")), ctx)
        result = await sbx_mod.glob_files(sbx, pattern, path)
        await _emit_terminal(ctx, f"glob {pattern} in {path}", result)
        return result["exit_code"] == 0, result["output"], None

    if tool == "grep":
        sbx = await ctx.ensure_sandbox()
        pattern = str(args.get("pattern", ""))
        path = _repo_path(str(args.get("path", ".")), ctx)
        result = await sbx_mod.grep_files(sbx, pattern, path)
        await _emit_terminal(ctx, f"grep -rn {pattern} {path}", result)
        return result["exit_code"] == 0, result["output"], None

    if tool == "repo_map":
        sbx = await ctx.ensure_sandbox()
        base = str(args.get("path") or "").strip()
        base = _repo_path(base, ctx) if base else (ctx.repo_dir or REPO_DIR)
        result = await sbx_mod.run_command(sbx, _repo_map_command(base), timeout=120)
        await _emit_terminal(ctx, f"repo_map {base}", result)
        return result["exit_code"] == 0, result["output"], None

    if tool == "deploy_site":
        from . import deploy

        return await deploy.deploy_site(ctx, args)

    if tool == "git_commit":
        import shlex

        sbx = await ctx.ensure_sandbox()
        message = str(args.get("message", "update"))
        cmd = f"cd {ctx.repo_dir} && git add -A && git commit -m {shlex.quote(message)}"
        result = await sbx_mod.run_command(sbx, cmd, timeout=120)
        await _emit_terminal(ctx, f"git add -A && git commit -m {shlex.quote(message)}", result)
        if result["exit_code"] != 0:
            if "nothing to commit" in result["output"]:
                return True, "nothing to commit (working tree clean)", None
            return False, result["output"], None
        branch = await sbx_mod.run_command(sbx, f"cd {ctx.repo_dir} && git rev-parse --abbrev-ref HEAD", timeout=30)
        ctx.extras["branch"] = branch["output"].strip()
        return True, result["output"] or "committed", None

    if tool == "git_push":
        sbx = await ctx.ensure_sandbox()
        cmd = f"cd {ctx.repo_dir} && git push -u origin HEAD 2>&1 | sed -e 's|x-access-token:[^@]*@|x-access-token:***@|g'"
        result = await sbx_mod.run_command(sbx, cmd, timeout=180)
        await _emit_terminal(ctx, "git push -u origin HEAD", result)
        branch = await sbx_mod.run_command(sbx, f"cd {ctx.repo_dir} && git rev-parse --abbrev-ref HEAD", timeout=30)
        if branch["exit_code"] == 0 and branch["output"].strip():
            ctx.extras["branch"] = branch["output"].strip()
        return result["exit_code"] == 0, result["output"], None

    if tool == "create_pr":
        import shlex

        sbx = await ctx.ensure_sandbox()
        title = str(args.get("title", "Update from AI agent"))
        body = str(args.get("body", ""))
        cmd = (
            f"cd {ctx.repo_dir} && gh pr create --title {shlex.quote(title)}"
            f" --body {shlex.quote(body)} --base {shlex.quote(ctx.default_branch)} 2>&1"
            f" | sed -e 's|x-access-token:[^@]*@|x-access-token:***@|g'"
        )
        result = await sbx_mod.run_command(sbx, cmd, timeout=120)
        await _emit_terminal(ctx, "gh pr create --title ... --body ...", result)
        if result["exit_code"] != 0:
            return False, result["output"], None
        m = re.search(r"https?://[^\s]*?/pull/(\d+)", result["output"])
        data = {"pr_url": None, "pr_number": None}
        if m:
            data["pr_url"] = m.group(0)
            data["pr_number"] = int(m.group(1))
            ctx.extras["pr_url"] = data["pr_url"]
            ctx.extras["pr_number"] = data["pr_number"]
            summary = f"created PR #{data['pr_number']}: {data['pr_url']}"
        else:
            summary = result["output"] or "PR created"
        return True, summary, data

    if tool == "web_fetch":
        url = str(args.get("url", ""))
        if not re.match(r"^https?://", url):
            return False, "url must start with http:// or https://", None
        try:
            async with httpx.AsyncClient(
                timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (compatible; AIAgent/1.0)"}
            ) as client:
                resp = await client.get(url)
        except httpx.HTTPError as exc:
            return False, f"fetch error: {exc}", None
        if resp.status_code >= 400:
            return False, f"HTTP {resp.status_code} for {url}", None
        content_type = resp.headers.get("content-type", "")
        text = resp.text
        if "html" in content_type or text.lstrip()[:1] == "<":
            text = strip_html(text)
        truncated = len(text) > MAX_WEBFETCH_CHARS
        text = text[:MAX_WEBFETCH_CHARS]
        return True, text, {"truncated": truncated}

    if tool == "browser_open":
        ok, summary, _ = await _run_browser(ctx, "open", {"url": str(args.get("url", ""))})
        return ok, summary, None

    if tool == "browser_click":
        ok, summary, _ = await _run_browser(ctx, "click", {"selector": str(args.get("selector", ""))})
        return ok, summary, None

    if tool == "browser_type":
        ok, summary, _ = await _run_browser(
            ctx, "type", {"selector": str(args.get("selector", "")), "text": str(args.get("text", ""))}
        )
        return ok, summary, None

    if tool == "browser_read_page":
        ok, summary, _ = await _run_browser(ctx, "read_page", {})
        return ok, summary, None

    if tool == "browser_screenshot":
        ok, summary, out = await _run_browser(ctx, "screenshot", {})
        if not ok:
            return False, summary, None
        data_bytes = await _read_screenshot_bytes(ctx)
        if not data_bytes:
            return False, "failed to read screenshot bytes from sandbox", None
        artifact_id = new_id()
        async with SessionLocal() as session:
            artifact = Artifact(
                id=artifact_id,
                task_id=ctx.task_id,
                kind="screenshot",
                filename="screenshot.png",
                mime="image/png",
                size=len(data_bytes),
                data=data_bytes,
                created_at=datetime.now(timezone.utc),
            )
            session.add(artifact)
            await session.commit()
        await _emit(
            ctx.task_id,
            "screenshot",
            {
                "artifact_id": artifact_id,
                "filename": "screenshot.png",
                "url": f"/api/artifacts/{artifact_id}",
            },
        )
        return True, f"screenshot saved ({len(data_bytes)} bytes)", {"artifact_id": artifact_id}

    if tool == "preview_file":
        sbx = await ctx.ensure_sandbox()
        raw_path = str(args.get("path", "") or "").strip()
        if not raw_path:
            return False, "path is required", None
        path = _repo_path(raw_path, ctx)
        try:
            data = await sbx.files.read(path, format="bytes")
        except Exception as exc:
            return False, f"cannot read {path}: {exc}", None
        if data is None:
            return False, f"file not found: {path}", None
        if isinstance(data, str):
            data = data.encode("utf-8")
        if not isinstance(data, bytes):
            return False, f"unexpected file data for {path}", None
        if not data:
            return False, f"file is empty: {path}", None
        if len(data) > MAX_PREVIEW_BYTES:
            return False, f"file too large to preview ({len(data)} bytes, max {MAX_PREVIEW_BYTES})", None
        filename = path.rsplit("/", 1)[-1] or "file"
        mime = guess_mime(filename)
        title = str(args.get("title") or "").strip() or filename
        artifact_id = new_id()
        async with SessionLocal() as session:
            artifact = Artifact(
                id=artifact_id,
                task_id=ctx.task_id,
                kind="file",
                filename=filename,
                mime=mime,
                size=len(data),
                data=data,
                created_at=datetime.now(timezone.utc),
            )
            session.add(artifact)
            await session.commit()
        await _emit(
            ctx.task_id,
            "file",
            {
                "artifact_id": artifact_id,
                "filename": filename,
                "mime": mime,
                "title": title,
                "url": f"/api/artifacts/{artifact_id}",
            },
        )
        return (
            True,
            f"preview published: {filename} ({mime}, {len(data)} bytes)",
            {"artifact_id": artifact_id, "filename": filename, "mime": mime, "title": title},
        )

    if tool == "finish":
        result_summary = str(args.get("result_summary", "") or "task finished")
        return True, result_summary, {"result_summary": result_summary}

    from . import connector_tools

    handled, c_ok, c_summary, c_data = await connector_tools.maybe_dispatch(ctx, tool, args)
    if handled:
        return c_ok, c_summary, c_data

    return False, f"unknown tool: {tool}", None
