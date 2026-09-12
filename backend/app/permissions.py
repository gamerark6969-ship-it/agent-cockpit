import fnmatch
from typing import Optional

DEFAULT_TOOL_LEVELS = {
    "browser_*": "auto",
    "web_fetch": "auto",
    "git_push": "auto",
}


def _bash_rules(settings_data: dict) -> list:
    perms = (settings_data or {}).get("permissions", {})
    rules = perms.get("bash_rules", [])
    out = []
    for r in rules or []:
        if isinstance(r, dict) and r.get("match") and r.get("level"):
            out.append((str(r["match"]), str(r["level"])))
    return out


def _tool_levels(settings_data: dict) -> dict:
    perms = (settings_data or {}).get("permissions", {})
    levels = perms.get("tool_levels", {}) or {}
    return {str(k): str(v) for k, v in levels.items() if v in ("auto", "ask", "deny")}


def evaluate(tool: str, args: Optional[dict], settings_data: dict) -> str:
    """Evaluate permission for a tool call. Returns 'auto' | 'ask' | 'deny'."""
    args = args or {}

    if tool == "bash":
        command = str(args.get("command", ""))
        for match, level in _bash_rules(settings_data):
            if fnmatch.fnmatchcase(command, match):
                return level
        return "auto"

    levels = _tool_levels(settings_data)
    # exact match first, then glob patterns
    if tool in levels:
        return levels[tool]
    for pattern, level in levels.items():
        if any(ch in pattern for ch in "*?[") and fnmatch.fnmatchcase(tool, pattern):
            return level

    # safe defaults
    if tool in ("read_file", "write_file", "edit_file", "list_dir", "glob", "grep",
                "git_commit", "finish"):
        return "auto"
    if tool == "create_pr":
        return "ask"
    if tool.startswith("browser_"):
        return "auto"
    if tool == "web_fetch":
        return "auto"
    return "auto"
