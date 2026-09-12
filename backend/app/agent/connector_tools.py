import asyncio
import email
import html as html_mod
import imaplib
import json
import re
import smtplib
import ssl
from email.header import decode_header, make_header
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .. import connectors as connectors_mod

GH_API = "https://api.github.com"
HTTP_TIMEOUT = 30


# ── tool schemas ─────────────────────────────────────────

CONNECTOR_TOOL_DEFINITIONS: List[dict] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the public web and return the top results (title, URL, snippet). Use before web_fetch to find pages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "description": "Max results (default 8)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_search",
            "description": "List emails from the connected Gmail inbox. Returns uid, from, subject, date. IMAP search syntax (e.g. 'UNSEEN', 'FROM \"x@y.com\"', 'SINCE 1-Jan-2024'); default ALL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "IMAP search criteria (default ALL)"},
                    "limit": {"type": "integer", "description": "Max messages (default 10)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_read",
            "description": "Read the full body of a Gmail message by its uid (from gmail_search).",
            "parameters": {
                "type": "object",
                "properties": {"uid": {"type": "string"}},
                "required": ["uid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_send",
            "description": "Send an email from the connected Gmail account. Requires user approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_whoami",
            "description": "Return the authenticated GitHub user (login, name) for the connected GitHub account.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_list_repos",
            "description": "List repositories the authenticated GitHub user can access (most recently updated first).",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "Max repos (default 30)"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_list_issues",
            "description": "List issues for a repository (owner/repo). Set state to 'open' (default), 'closed' or 'all'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "owner/repo"},
                    "state": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["repo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_create_issue",
            "description": "Create an issue in a repository (owner/repo). Requires user approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "owner/repo"},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["repo", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_read_file",
            "description": "Read a file from a GitHub repository via the API (owner/repo, path, optional ref).",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "owner/repo"},
                    "path": {"type": "string"},
                    "ref": {"type": "string", "description": "branch/tag/sha (optional)"},
                },
                "required": ["repo", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "slack_post_message",
            "description": "Post a message to Slack (channel + text). Uses the connected Slack token or webhook.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "Channel id/name (ignored for webhooks)"},
                    "text": {"type": "string"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notion_search",
            "description": "Search the connected Notion workspace for pages and databases matching a query.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]


# ── helpers ──────────────────────────────────────────────

_SCRIPT_RE = re.compile(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>")
_TAG_RE = re.compile(r"<[^>]+>")


def _plain(text: str) -> str:
    text = _SCRIPT_RE.sub(" ", text or "")
    text = _TAG_RE.sub(" ", text)
    text = html_mod.unescape(text)
    return re.sub(r"\s+\n", "\n", re.sub(r"[ \t]+", " ", text)).strip()


def _hdr(value: str) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return value or ""


def _extract_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(
                part.get("Content-Disposition") or ""
            ):
                try:
                    return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
                except Exception:
                    continue
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    return _plain(part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace"))
                except Exception:
                    continue
        return ""
    try:
        payload = msg.get_payload(decode=True)
        if payload is None:
            return str(msg.get_payload() or "")
        text = payload.decode(msg.get_content_charset() or "utf-8", "replace")
        return _plain(text) if msg.get_content_type() == "text/html" else text
    except Exception:
        return str(msg.get_payload() or "")


# ── Gmail (IMAP + SMTP) ──────────────────────────────────


def _gmail_credentials(cfg: Dict[str, Any]) -> Tuple[str, str]:
    address = (cfg.get("email") or "").strip()
    password = (cfg.get("password") or "").strip()
    if not address or not password:
        raise RuntimeError(
            "Gmail connector is not configured. Add the Gmail address and a Google App Password in Connectors."
        )
    return address, password


def _gmail_search_sync(cfg: Dict[str, Any], query: str, limit: int) -> List[Dict[str, str]]:
    address, password = _gmail_credentials(cfg)
    host = cfg.get("imap_host") or "imap.gmail.com"
    conn = imaplib.IMAP4_SSL(host, 993)
    try:
        conn.login(address, password)
        conn.select("INBOX")
        criteria = query.strip() or "ALL"
        typ, data = conn.search(None, criteria)
        if typ != "OK":
            raise RuntimeError(f"IMAP search failed: {data}")
        ids = data[0].split()
        ids = ids[-max(1, min(limit, 50)):][::-1]
        out: List[Dict[str, str]] = []
        for uid in ids:
            typ, msg_data = conn.fetch(uid, "(RFC822.HEADER)")
            if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            out.append(
                {
                    "uid": uid.decode(),
                    "from": _hdr(msg.get("From", "")),
                    "subject": _hdr(msg.get("Subject", "")),
                    "date": msg.get("Date", ""),
                }
            )
        return out
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _gmail_read_sync(cfg: Dict[str, Any], uid: str) -> Dict[str, str]:
    address, password = _gmail_credentials(cfg)
    host = cfg.get("imap_host") or "imap.gmail.com"
    conn = imaplib.IMAP4_SSL(host, 993)
    try:
        conn.login(address, password)
        conn.select("INBOX")
        typ, data = conn.fetch(str(uid).encode(), "(RFC822)")
        if typ != "OK" or not data or not isinstance(data[0], tuple):
            raise RuntimeError(f"message {uid} not found")
        msg = email.message_from_bytes(data[0][1])
        body = _extract_body(msg)
        return {
            "from": _hdr(msg.get("From", "")),
            "to": _hdr(msg.get("To", "")),
            "subject": _hdr(msg.get("Subject", "")),
            "date": msg.get("Date", ""),
            "body": body[:15000],
        }
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _gmail_send_sync(cfg: Dict[str, Any], to: str, subject: str, body: str) -> str:
    address, password = _gmail_credentials(cfg)
    host = cfg.get("smtp_host") or "smtp.gmail.com"
    port = int(cfg.get("smtp_port") or 587)
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = address
    msg["To"] = to
    with smtplib.SMTP(host, port, timeout=HTTP_TIMEOUT) as server:
        server.starttls(context=ssl.create_default_context())
        server.login(address, password)
        server.send_message(msg)
    return f"email sent to {to}"


# ── GitHub REST ──────────────────────────────────────────


async def _gh_request(cfg: Dict[str, Any], method: str, path: str, **kwargs) -> Any:
    pat = (cfg.get("pat") or "").strip()
    if not pat:
        raise RuntimeError("GitHub connector is not configured. Add a Personal Access Token in Connectors.")
    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "agent-cockpit",
    }
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.request(method, f"{GH_API}{path}", headers=headers, **kwargs)
    if resp.status_code >= 400:
        detail = resp.text[:400]
        try:
            detail = resp.json().get("message", detail)
        except Exception:
            pass
        raise RuntimeError(f"GitHub API {resp.status_code}: {detail}")
    if resp.status_code == 204 or not resp.content:
        return {}
    return resp.json()


# ── Slack / Notion ───────────────────────────────────────


async def _slack_post(cfg: Dict[str, Any], channel: str, text: str) -> str:
    token = (cfg.get("bot_token") or cfg.get("token") or "").strip()
    webhook = (cfg.get("webhook_url") or "").strip()
    if token:
        data = {"text": text}
        if channel:
            data["channel"] = channel
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {token}"},
                json=data,
            )
        payload = resp.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Slack error: {payload.get('error', 'unknown')}")
        return "message posted to Slack"
    if webhook:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(webhook, json={"text": text})
        if resp.status_code >= 400:
            raise RuntimeError(f"Slack webhook HTTP {resp.status_code}")
        return "message posted to Slack webhook"
    raise RuntimeError("Slack connector is not configured (add a bot token or webhook URL).")


async def _notion_search(cfg: Dict[str, Any], query: str) -> List[Dict[str, str]]:
    token = (cfg.get("token") or "").strip()
    if not token:
        raise RuntimeError("Notion connector is not configured (add an integration token).")
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(
            "https://api.notion.com/v1/search",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
            json={"query": query, "page_size": 10},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Notion API {resp.status_code}: {resp.text[:300]}")
    results = []
    for item in resp.json().get("results", []):
        title = ""
        props = item.get("properties") or {}
        for value in props.values():
            if isinstance(value, dict) and value.get("type") == "title":
                title = "".join(t.get("plain_text", "") for t in value.get("title", []))
                break
        if not title:
            title = "".join(t.get("plain_text", "") for t in (item.get("title") or []))
        results.append({"id": item.get("id", ""), "type": item.get("object", ""), "title": title})
    return results


# ── web search (DuckDuckGo HTML) ─────────────────────────


async def _web_search(query: str, limit: int) -> Tuple[bool, str, Optional[dict]]:
    if not query.strip():
        return False, "query must not be empty", None
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get("https://html.duckduckgo.com/html/", params={"q": query}, headers=headers)
    except httpx.HTTPError as exc:
        return False, f"search request failed: {exc}", None
    if resp.status_code >= 400:
        return False, f"search failed with HTTP {resp.status_code}", None
    html = resp.text
    results = []
    for match in re.finditer(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S
    ):
        url, title = match.group(1), _plain(match.group(2))
        results.append({"url": html_mod.unescape(url), "title": title})
        if len(results) >= max(1, min(limit, 15)):
            break
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.S)
    for i, snip in enumerate(snippets[: len(results)]):
        results[i]["snippet"] = _plain(snip)
    if not results:
        return True, "no results found", {"count": 0}
    lines = [f"{i + 1}. {r['title']}\n   {r['url']}\n   {r.get('snippet', '')}".rstrip() for i, r in enumerate(results)]
    return True, "\n".join(lines), {"count": len(results)}


# ── dispatch ─────────────────────────────────────────────

_PREFIXES = ("gmail_", "github_", "slack_", "notion_")


async def maybe_dispatch(ctx, tool: str, args: dict) -> Tuple[bool, bool, str, Optional[dict]]:
    """Returns (handled, ok, summary, data)."""
    if tool != "web_search" and not tool.startswith(_PREFIXES):
        return False, False, "", None
    try:
        if tool == "web_search":
            ok, summary, data = await _web_search(str(args.get("query", "")), int(args.get("limit") or 8))
            return True, ok, summary, data

        if tool.startswith("gmail_"):
            cfg = await connectors_mod.get_config("gmail")
            if tool == "gmail_search":
                rows = await asyncio.to_thread(
                    _gmail_search_sync, cfg, str(args.get("query", "")), int(args.get("limit") or 10)
                )
                if not rows:
                    return True, True, "no matching emails", {"count": 0}
                lines = [f"- uid={r['uid']} | {r['date']} | {r['from']} | {r['subject']}" for r in rows]
                return True, True, "\n".join(lines), {"count": len(rows)}
            if tool == "gmail_read":
                data = await asyncio.to_thread(_gmail_read_sync, cfg, str(args.get("uid", "")))
                summary = (
                    f"From: {data['from']}\nTo: {data['to']}\nDate: {data['date']}\n"
                    f"Subject: {data['subject']}\n\n{data['body']}"
                )
                return True, True, summary, None
            if tool == "gmail_send":
                summary = await asyncio.to_thread(
                    _gmail_send_sync,
                    cfg,
                    str(args.get("to", "")),
                    str(args.get("subject", "")),
                    str(args.get("body", "")),
                )
                return True, True, summary, None
            return True, False, f"unknown gmail tool: {tool}", None

        if tool.startswith("github_"):
            cfg = await connectors_mod.get_config("github")
            if tool == "github_whoami":
                user = await _gh_request(cfg, "GET", "/user")
                return True, True, f"login: {user.get('login')}\nname: {user.get('name')}", None
            if tool == "github_list_repos":
                repos = await _gh_request(
                    cfg, "GET", "/user/repos", params={"per_page": max(1, min(int(args.get("limit") or 30), 100)), "sort": "updated"}
                )
                lines = [f"- {r['full_name']} ({'private' if r.get('private') else 'public'})" for r in repos]
                return True, True, "\n".join(lines) or "no repositories", {"count": len(repos)}
            if tool == "github_list_issues":
                repo = str(args.get("repo", ""))
                issues = await _gh_request(
                    cfg,
                    "GET",
                    f"/repos/{repo}/issues",
                    params={
                        "state": str(args.get("state") or "open"),
                        "per_page": max(1, min(int(args.get("limit") or 20), 100)),
                    },
                )
                lines = [
                    f"- #{i['number']} [{i.get('state')}] {i.get('title')}"
                    for i in issues
                    if "pull_request" not in i
                ]
                return True, True, "\n".join(lines) or "no issues", {"count": len(lines)}
            if tool == "github_create_issue":
                issue = await _gh_request(
                    cfg,
                    "POST",
                    f"/repos/{args.get('repo', '')}/issues",
                    json={"title": str(args.get("title", "")), "body": str(args.get("body", ""))},
                )
                return True, True, f"created issue #{issue.get('number')}: {issue.get('html_url')}", {"url": issue.get("html_url")}
            if tool == "github_read_file":
                params = {}
                if args.get("ref"):
                    params["ref"] = str(args["ref"])
                payload = await _gh_request(
                    cfg,
                    "GET",
                    f"/repos/{args.get('repo', '')}/contents/{args.get('path', '')}",
                    params=params,
                )
                import base64 as _b64

                content = _b64.b64decode(payload.get("content", "")).decode("utf-8", "replace")
                return True, True, content[:15000], None
            return True, False, f"unknown github tool: {tool}", None

        if tool == "slack_post_message":
            cfg = await connectors_mod.get_config("slack")
            summary = await _slack_post(cfg, str(args.get("channel", "")), str(args.get("text", "")))
            return True, True, summary, None

        if tool == "notion_search":
            cfg = await connectors_mod.get_config("notion")
            rows = await _notion_search(cfg, str(args.get("query", "")))
            lines = [f"- [{r['type']}] {r['title']} ({r['id']})" for r in rows]
            return True, True, "\n".join(lines) or "no results", {"count": len(rows)}

        return True, False, f"unknown connector tool: {tool}", None
    except Exception as exc:
        return True, False, f"{tool} error: {type(exc).__name__}: {exc}", None
