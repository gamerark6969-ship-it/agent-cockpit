import type { TaskStatus } from "./api";

export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const diff = Date.now() - then;
  const sec = Math.round(diff / 1000);
  if (sec < 5) return "just now";
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  if (day < 30) return `${day}d ago`;
  return new Date(iso).toLocaleDateString();
}

export function elapsed(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const sec = Math.max(0, Math.round((Date.now() - then) / 1000));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

export function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export const STATUS_LABELS: Record<TaskStatus, string> = {
  queued: "Queued",
  running: "Running",
  awaiting_approval: "Needs approval",
  done: "Done",
  failed: "Failed",
  stopped: "Stopped",
};

export const STATUS_CLASSES: Record<TaskStatus, string> = {
  queued: "bg-surface text-muted border-line",
  running: "bg-blue-950/40 text-blue-300 border-blue-900/60",
  awaiting_approval: "bg-warn/10 text-warn border-warn/35",
  done: "bg-surface text-fg border-line",
  failed: "bg-red-950/25 text-del border-red-950",
  stopped: "bg-surface text-faint border-line",
};

export function truncate(text: string, max = 400): string {
  if (text.length <= max) return text;
  return `${text.slice(0, max)}…`;
}

export function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
