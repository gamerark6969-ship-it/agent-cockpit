from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ── Projects ─────────────────────────────────────────────


class ProjectCreate(BaseModel):
    repo_url: Optional[str] = None
    name: Optional[str] = None
    default_branch: Optional[str] = None
    kind: Literal["repo", "chat"] = "repo"


class ProjectOut(BaseModel):
    id: str
    name: str
    kind: str = "repo"
    repo_url: Optional[str] = None
    default_branch: str
    settings: dict = Field(default_factory=dict)
    created_at: datetime


# ── Tasks ────────────────────────────────────────────────


class TaskCreate(BaseModel):
    prompt: str
    model: Optional[str] = None


class TaskOut(BaseModel):
    id: str
    project_id: str
    prompt: str
    status: str
    model: Optional[str]
    iterations: int
    tokens_used: int
    error: Optional[str]
    result_summary: Optional[str]
    created_at: datetime
    updated_at: datetime


# ── Events ───────────────────────────────────────────────


class EventOut(BaseModel):
    seq: int
    task_id: str
    type: str
    payload: dict
    created_at: datetime


# ── Approvals ────────────────────────────────────────────


class ApprovalOut(BaseModel):
    id: str
    task_id: str
    kind: str
    description: str
    payload: dict
    created_at: datetime


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "deny"]


# ── Diff ─────────────────────────────────────────────────


class DiffFile(BaseModel):
    path: str
    status: str
    additions: int
    deletions: int


class DiffOut(BaseModel):
    summary: str
    files: List[DiffFile]


# ── Screenshots ──────────────────────────────────────────


class ScreenshotOut(BaseModel):
    id: str
    filename: str
    created_at: datetime


# ── Push ─────────────────────────────────────────────────


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscribe(BaseModel):
    endpoint: str
    keys: PushKeys


class PushUnsubscribe(BaseModel):
    endpoint: str


# ── Steer / Stop ─────────────────────────────────────────


class SteerRequest(BaseModel):
    message: str


# ── Settings ─────────────────────────────────────────────

PermissionLevel = Literal["auto", "ask", "deny"]


class BashRule(BaseModel):
    match: str
    level: PermissionLevel


class Permissions(BaseModel):
    bash_rules: List[BashRule] = Field(
        default_factory=lambda: [
            BashRule(match="sudo *", level="deny"),
            BashRule(match="git push --force*", level="deny"),
            BashRule(match="rm -rf /*", level="deny"),
            BashRule(match="gh pr create*", level="ask"),
            BashRule(match="*curl*|*sh", level="ask"),
        ]
    )
    tool_levels: dict = Field(
        default_factory=lambda: {
            "browser_*": "auto",
            "web_fetch": "auto",
            "web_search": "auto",
            "git_push": "auto",
            "gmail_read": "auto",
            "gmail_search": "auto",
            "gmail_send": "ask",
            "slack_post_message": "ask",
            "github_create_issue": "ask",
        }
    )

    @field_validator("tool_levels")
    @classmethod
    def _validate_levels(cls, v: dict) -> dict:
        for k, lvl in v.items():
            if lvl not in ("auto", "ask", "deny"):
                raise ValueError(f"invalid permission level '{lvl}' for tool '{k}'")
        return v


class SettingsOut(BaseModel):
    default_model: str = "deepseek-v4.1-flash"
    max_iterations: int = Field(default=50, ge=1)
    token_budget: int = Field(default=2000000, ge=1)
    command_timeout_s: int = Field(default=600, ge=1)
    approval_timeout_s: int = Field(default=1800, ge=1)
    permissions: Permissions = Field(default_factory=Permissions)


class SettingsIn(SettingsOut):
    pass


# ── Models proxy ─────────────────────────────────────────


class ModelsOut(BaseModel):
    models: List[str]


# ── Connectors ───────────────────────────────────────────


class ConnectorCreate(BaseModel):
    kind: str
    name: Optional[str] = None
    config: dict = Field(default_factory=dict)
    enabled: bool = True


class ConnectorUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[dict] = None
    enabled: Optional[bool] = None


class ConnectorOut(BaseModel):
    id: str
    kind: str
    name: str
    enabled: bool
    config: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: Optional[datetime] = None
