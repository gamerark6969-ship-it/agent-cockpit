const TOKEN_KEY = "app_token";

// In dev this is empty and Vite proxies /api to the local backend. In production
// set VITE_API_BASE to the deployed backend origin (e.g. https://user-space.hf.space).
export const API_BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/+$/, "");

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (res.status === 401) {
    clearToken();
    const loginPath = `${import.meta.env.BASE_URL || "/"}login`;
    if (window.location.pathname !== loginPath) {
      window.location.assign(loginPath);
    }
    throw new ApiError(401, "unauthorized");
  }
  if (!res.ok) {
    let detail = res.statusText || `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body && body.detail) detail = body.detail;
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("Content-Type") || "";
  if (ct.includes("application/json")) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

// ── types ───────────────────────────────────────────────

export type TaskStatus =
  | "queued"
  | "running"
  | "awaiting_approval"
  | "done"
  | "failed"
  | "stopped";

export interface Project {
  id: string;
  name: string;
  kind: string;
  repo_url: string | null;
  default_branch: string;
  settings: Record<string, unknown>;
  created_at: string;
}

export interface Connector {
  id: string;
  kind: string;
  name: string;
  enabled: boolean;
  config: Record<string, string>;
  created_at: string;
  updated_at: string | null;
}

export interface Task {
  id: string;
  project_id: string;
  prompt: string;
  status: TaskStatus;
  model: string;
  iterations: number;
  tokens_used: number;
  error: string | null;
  result_summary: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentEvent {
  seq: number;
  task_id: string;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface Approval {
  id: string;
  task_id: string;
  kind: string;
  description: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface BashRule {
  match: string;
  level: "auto" | "ask" | "deny";
}

export interface AppSettings {
  default_model: string;
  max_iterations: number;
  token_budget: number;
  command_timeout_s: number;
  approval_timeout_s: number;
  compaction_threshold_tokens?: number;
  permissions: {
    bash_rules: BashRule[];
    tool_levels?: Record<string, string>;
  };
}

export interface DiffFile {
  path: string;
  status: string;
  additions: number;
  deletions: number;
}

export interface DiffResult {
  summary: string;
  files: DiffFile[];
}

export interface Screenshot {
  id: string;
  filename: string;
  created_at: string;
}

export interface PushSubscriptionInput {
  endpoint: string;
  keys: { p256dh: string; auth: string };
}

// ── endpoints ───────────────────────────────────────────

export function getHealth() {
  return apiFetch<{ status: string; version: string }>("/api/health");
}

export function listProjects() {
  return apiFetch<Project[]>("/api/projects");
}

export function getProject(id: string) {
  return apiFetch<Project>(`/api/projects/${id}`);
}

export function createProject(body: {
  repo_url?: string;
  name?: string;
  default_branch?: string;
  kind?: "repo" | "chat";
}) {
  return apiFetch<Project>("/api/projects", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function deleteProject(id: string) {
  return apiFetch<void>(`/api/projects/${id}`, { method: "DELETE" });
}

export function listTasks(projectId: string) {
  return apiFetch<Task[]>(`/api/projects/${projectId}/tasks`);
}

export function createTask(projectId: string, body: { prompt: string; model?: string }) {
  return apiFetch<Task>(`/api/projects/${projectId}/tasks`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getTask(id: string) {
  return apiFetch<Task>(`/api/tasks/${id}`);
}

export function stopTask(id: string) {
  return apiFetch<void>(`/api/tasks/${id}/stop`, { method: "POST" });
}

export function steerTask(id: string, message: string) {
  return apiFetch<void>(`/api/tasks/${id}/steer`, {
    method: "POST",
    body: JSON.stringify({ message }),
  });
}

export function getEvents(taskId: string, after = 0) {
  return apiFetch<AgentEvent[]>(`/api/tasks/${taskId}/events?after=${after}`);
}

export function getDiff(taskId: string) {
  return apiFetch<DiffResult>(`/api/tasks/${taskId}/diff`);
}

export function getScreenshots(taskId: string) {
  return apiFetch<Screenshot[]>(`/api/tasks/${taskId}/screenshots`);
}

export async function getArtifactObjectUrl(artifactId: string): Promise<string> {
  const res = await fetch(`${API_BASE}/api/artifacts/${artifactId}`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  if (!res.ok) throw new ApiError(res.status, "failed to load artifact");
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

export function listApprovals(pending = false) {
  const q = pending ? "?pending=true" : "";
  return apiFetch<Approval[]>(`/api/approvals${q}`);
}

export function decideApproval(id: string, decision: "approve" | "deny") {
  return apiFetch<Approval>(`/api/approvals/${id}/decision`, {
    method: "POST",
    body: JSON.stringify({ decision }),
  });
}

export function subscribePush(sub: PushSubscriptionInput) {
  return apiFetch<void>("/api/push/subscribe", {
    method: "POST",
    body: JSON.stringify(sub),
  });
}

export function getVapidPublicKey() {
  return apiFetch<{ public_key: string }>("/api/push/vapid-public");
}

export function getSettings() {
  return apiFetch<AppSettings>("/api/settings");
}

export function putSettings(body: AppSettings) {
  return apiFetch<AppSettings>("/api/settings", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function listModels() {
  return apiFetch<{ models: string[] }>("/api/models");
}

// ── connectors ──────────────────────────────────────────

export function listConnectors() {
  return apiFetch<Connector[]>("/api/connectors");
}

export function createConnector(body: {
  kind: string;
  name?: string;
  config?: Record<string, string>;
  enabled?: boolean;
}) {
  return apiFetch<Connector>("/api/connectors", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateConnector(
  id: string,
  body: { name?: string; config?: Record<string, string>; enabled?: boolean },
) {
  return apiFetch<Connector>(`/api/connectors/${id}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function deleteConnector(id: string) {
  return apiFetch<void>(`/api/connectors/${id}`, { method: "DELETE" });
}
