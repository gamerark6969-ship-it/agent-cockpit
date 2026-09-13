import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  createTask,
  getEvents,
  getProject,
  getSettings,
  listModels,
  listTasks,
  stopTask,
  type AgentEvent,
  type Project,
  type Task,
  type TaskStatus,
} from "../lib/api";
import { streamTaskEvents, type StreamHandle } from "../lib/sse";
import EventCard, { WorkLog, groupFeed } from "../components/EventCard";
import Spinner from "../components/Spinner";
import Markdown from "../components/Markdown";
import { CheckIcon, ChevronIcon, SendIcon, StopIcon } from "../components/Icons";

const TERMINAL: TaskStatus[] = ["done", "failed", "stopped"];
const FALLBACK_MODELS = ["deepseek-v4.1-flash"];

const MODEL_LABELS: Record<string, string> = {
  "deepseek-v4.1-flash": "DeepSeek V4.1 Flash",
  "gemini-3.8-flash": "Gemini 3.8 Flash",
};

function prettyModel(id: string): string {
  if (MODEL_LABELS[id]) return MODEL_LABELS[id];
  return id.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function ModelSheet({
  models,
  value,
  onSelect,
  onClose,
}: {
  models: string[];
  value: string;
  onSelect: (m: string) => void;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-end bg-black/60" onClick={onClose}>
      <div
        className="safe-bottom w-full rounded-t-3xl border-t border-zinc-800 bg-zinc-950 px-4 pt-3"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-zinc-700" />
        <p className="mb-3 px-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">Model</p>
        <div className="flex flex-col gap-1.5 pb-5">
          {models.map((m) => (
            <button
              key={m}
              onClick={() => {
                onSelect(m);
                onClose();
              }}
              className={`flex h-12 items-center justify-between rounded-xl border px-3.5 text-left text-sm transition-colors ${
                m === value
                  ? "border-emerald-700/60 bg-emerald-950/30 text-emerald-200"
                  : "border-zinc-800 bg-zinc-900/50 text-zinc-300 active:bg-zinc-800"
              }`}
            >
              <span className="flex flex-col">
                <span className="font-medium">{prettyModel(m)}</span>
                <span className="font-mono text-[10px] text-zinc-500">{m}</span>
              </span>
              {m === value ? <CheckIcon className="h-4 w-4 text-emerald-400" /> : null}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

const str = (v: unknown, fallback = ""): string => (typeof v === "string" ? v : fallback);

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] whitespace-pre-wrap rounded-3xl rounded-br-lg bg-emerald-600 px-4 py-2.5 text-sm leading-relaxed text-white">
        {text}
      </div>
    </div>
  );
}

function AssistantBubble({ text }: { text: string }) {
  if (!text.trim()) return null;
  return (
    <div className="max-w-[92%] rounded-3xl rounded-tl-lg border border-zinc-800/80 bg-zinc-900/60 px-4 py-2.5">
      <Markdown text={text} />
    </div>
  );
}

function StreamingBubble({ text }: { text: string }) {
  return (
    <div className="max-w-[92%] rounded-3xl rounded-tl-lg border border-zinc-800/80 bg-zinc-900/60 px-4 py-2.5">
      <Markdown text={text} />
      <span className="ml-0.5 inline-block h-4 w-1.5 translate-y-0.5 animate-pulse rounded-sm bg-emerald-400" />
    </div>
  );
}

function StatusRow({ status }: { status: TaskStatus }) {
  const label =
    status === "queued"
      ? "Queued…"
      : status === "awaiting_approval"
        ? "Waiting for approval…"
        : "Working…";
  return (
    <div className="flex items-center gap-2 px-1 text-xs text-zinc-500">
      <Spinner className="h-3.5 w-3.5" /> {label}
    </div>
  );
}

export default function Chat() {
  const { id = "" } = useParams();
  const [project, setProject] = useState<Project | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [eventsByTask, setEventsByTask] = useState<Record<string, AgentEvent[]>>({});
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [modelOpen, setModelOpen] = useState(false);

  const handles = useRef<Map<string, StreamHandle>>(new Map());
  const feedRef = useRef<HTMLDivElement | null>(null);
  const stick = useRef(true);

  const mergeEvents = (taskId: string, incoming: AgentEvent[]) => {
    if (!incoming.length) return;
    setEventsByTask((prev) => {
      const existing = prev[taskId] ?? [];
      const seen = new Set(existing.map((e) => e.seq));
      const fresh = incoming.filter((e) => !seen.has(e.seq));
      if (!fresh.length) return prev;
      return { ...prev, [taskId]: [...existing, ...fresh].sort((a, b) => a.seq - b.seq) };
    });
  };

  const closeStream = (taskId: string) => {
    const h = handles.current.get(taskId);
    if (h) {
      h.close();
      handles.current.delete(taskId);
    }
  };

  const closeAll = () => {
    handles.current.forEach((h) => h.close());
    handles.current.clear();
  };

  const applyTerminalEvent = (e: AgentEvent) => {
    if (e.type !== "task_completed" && e.type !== "task_failed" && e.type !== "task_stopped") return;
    closeStream(e.task_id);
    const status: TaskStatus =
      e.type === "task_completed" ? "done" : e.type === "task_failed" ? "failed" : "stopped";
    setTasks((prev) =>
      prev.map((t) =>
        t.id === e.task_id
          ? {
              ...t,
              status,
              result_summary:
                e.type === "task_completed"
                  ? String(e.payload.result_summary ?? t.result_summary ?? "")
                  : t.result_summary,
              error: e.type === "task_failed" ? String(e.payload.error ?? "") : t.error,
            }
          : t,
      ),
    );
  };

  const openStream = (task: Task) => {
    if (handles.current.has(task.id) || TERMINAL.includes(task.status)) return;
    const placeholder: StreamHandle = { close: () => undefined };
    handles.current.set(task.id, placeholder);
    void (async () => {
      try {
        const history = await getEvents(task.id, 0);
        mergeEvents(task.id, history);
        if (handles.current.get(task.id) !== placeholder) return;
        const last = history.length ? history[history.length - 1].seq : 0;
        const handle = streamTaskEvents(task.id, {
          after: last,
          onEvent: (e) => {
            mergeEvents(task.id, [e]);
            applyTerminalEvent(e);
          },
        });
        if (handles.current.get(task.id) !== placeholder) {
          handle.close();
          return;
        }
        handles.current.set(task.id, handle);
      } catch (err) {
        if (handles.current.get(task.id) === placeholder) handles.current.delete(task.id);
        setError(err instanceof Error ? err.message : "failed to load messages");
      }
    })();
  };

  const refreshTasks = async () => {
    const rows = await listTasks(id);
    const sorted = [...rows].sort((a, b) => a.created_at.localeCompare(b.created_at));
    setTasks(sorted);
    for (const t of sorted) if (!TERMINAL.includes(t.status)) openStream(t);
    return sorted;
  };

  useEffect(() => {
    let alive = true;
    setLoading(true);
    closeAll();
    setEventsByTask({});
    void (async () => {
      try {
        const [p, rows] = await Promise.all([getProject(id), listTasks(id)]);
        if (!alive) return;
        setProject(p);
        const sorted = [...rows].sort((a, b) => a.created_at.localeCompare(b.created_at));
        setTasks(sorted);
        for (const t of sorted) if (!TERMINAL.includes(t.status)) openStream(t);
        const m = await listModels()
          .then((r) => r.models)
          .catch(() => [] as string[]);
        const s = await getSettings().catch(() => null);
        if (!alive) return;
        const opts = m.length ? m : FALLBACK_MODELS;
        setModels(opts);
        setModel(s?.default_model && opts.includes(s.default_model) ? s.default_model : opts[0]);
      } catch (err) {
        if (alive) setError(err instanceof Error ? err.message : "failed to load chat");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
      closeAll();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const anyActive = tasks.some((t) => !TERMINAL.includes(t.status));

  useEffect(() => {
    if (!anyActive) return;
    // SSE keeps the feed live; we only reconcile task statuses when the tab
    // regains focus (cheap, and avoids the old 4s polling loop).
    const onFocus = () => {
      if (document.visibilityState === "visible") void refreshTasks().catch(() => undefined);
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anyActive, id]);

  useEffect(() => {
    const el = feedRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [eventsByTask, tasks]);

  const send = async () => {
    const prompt = input.trim();
    if (!prompt || busy) return;
    setBusy(true);
    setError("");
    try {
      const task = await createTask(id, { prompt, model: model || undefined });
      setInput("");
      setTasks((prev) => [...prev, task].sort((a, b) => a.created_at.localeCompare(b.created_at)));
      stick.current = true;
      openStream(task);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to send message");
    } finally {
      setBusy(false);
    }
  };

  const doStop = async () => {
    const target = [...tasks].reverse().find((t) => !TERMINAL.includes(t.status));
    if (!target) return;
    try {
      await stopTask(target.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to stop");
    }
  };

  const decided = useMemo(() => {
    const map: Record<string, "approved" | "denied"> = {};
    for (const list of Object.values(eventsByTask)) {
      for (const e of list) {
        if (e.type !== "approval_decision") continue;
        const aid = String(e.payload.approval_id ?? "");
        const decision = String(e.payload.decision ?? "");
        if (aid && (decision === "approve" || decision === "deny")) {
          map[aid] = decision === "approve" ? "approved" : "denied";
        }
      }
    }
    return map;
  }, [eventsByTask]);

  const streamText = useMemo(() => {
    const map: Record<string, string> = {};
    for (const [tid, list] of Object.entries(eventsByTask)) {
      let buf = "";
      for (const e of list) {
        if (e.type === "agent_delta") buf += str(e.payload.content);
        else if (e.type === "agent_message") buf = "";
      }
      if (buf) map[tid] = buf;
    }
    return map;
  }, [eventsByTask]);

  const onScroll = () => {
    const el = feedRef.current;
    if (!el) return;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  };

  if (loading) {
    return (
      <div className="flex justify-center py-16 text-zinc-500">
        <Spinner />
      </div>
    );
  }

  if (!project) {
    return (
      <div className="px-4 pt-6">
        <p className="text-sm text-red-400">{error || "chat not found"}</p>
      </div>
    );
  }

  return (
    <div className="safe-top flex h-dvh flex-col">
      <header className="flex items-center gap-2 border-b border-zinc-800/60 px-3 py-2.5">
        <Link
          to="/"
          className="flex h-9 w-9 items-center justify-center rounded-full text-zinc-400 transition-colors active:bg-zinc-800 active:text-zinc-100"
          aria-label="Back"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-5 w-5">
            <path strokeLinecap="round" strokeLinejoin="round" d="m15 6-6 6 6 6" />
          </svg>
        </Link>
        <h1 className="min-w-0 flex-1 truncate text-[15px] font-semibold text-zinc-100">
          {project.name}
        </h1>
        {anyActive ? (
          <span className="flex shrink-0 items-center gap-1.5 rounded-full bg-emerald-500/10 px-2.5 py-1 text-[11px] font-medium text-emerald-400">
            <span className="pulse-dot h-1.5 w-1.5 rounded-full bg-emerald-400" />
            live
          </span>
        ) : null}
      </header>

      <div
        ref={feedRef}
        onScroll={onScroll}
        className="flex flex-1 flex-col gap-4 overflow-y-auto px-3 py-4"
      >
        {tasks.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 py-16 text-center">
            <div className="flex h-14 w-14 items-center justify-center rounded-3xl bg-emerald-500/10 text-emerald-400 ring-1 ring-emerald-500/20">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-7 w-7">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M21 12a8 8 0 0 1-8 8H5l-2 2V12a8 8 0 0 1 8-8h2a8 8 0 0 1 8 8Z"
                />
              </svg>
            </div>
            <p className="text-base font-semibold text-zinc-300">Ask me anything</p>
            <p className="max-w-xs text-xs leading-relaxed text-zinc-500">
              I can build and preview files, search the web, read and send email, use GitHub, and
              more.
            </p>
          </div>
        ) : (
          tasks.map((task) => {
            const events = (eventsByTask[task.id] ?? []).filter(
              (e) => e.type !== "checkpoint" && e.type !== "task_started",
            );
            const rows = groupFeed(events);
            const active = !TERMINAL.includes(task.status);
            return (
              <div key={task.id} className="flex flex-col gap-2.5">
                <UserBubble text={task.prompt} />
                {rows.map((row) =>
                  row.kind === "work" ? (
                    <WorkLog
                      key={`w-${row.key}`}
                      events={row.events}
                      decidedApprovals={decided}
                      onDecided={() => undefined}
                    />
                  ) : (
                    <EventCard
                      key={row.event.seq}
                      event={row.event}
                      compact
                      decidedApprovals={decided}
                      onDecided={() => undefined}
                    />
                  ),
                )}
                {streamText[task.id] ? <StreamingBubble text={streamText[task.id]} /> : null}
                {active ? <StatusRow status={task.status} /> : null}
                {task.status === "done" && !events.some((e) => e.type === "task_completed") ? (
                  <AssistantBubble text={task.result_summary || ""} />
                ) : null}
                {task.status === "failed" && !events.some((e) => e.type === "task_failed") ? (
                  <div className="rounded-2xl border border-red-800/70 bg-red-950/25 px-4 py-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-red-300">
                      Failed
                    </p>
                    <p className="mt-1 whitespace-pre-wrap text-sm text-zinc-300">
                      {task.error || "the agent hit an error"}
                    </p>
                  </div>
                ) : null}
                {task.status === "stopped" && !events.some((e) => e.type === "task_stopped") ? (
                  <div className="rounded-2xl border border-zinc-700 bg-zinc-900/60 px-4 py-2.5">
                    <p className="text-xs text-zinc-400">Stopped</p>
                  </div>
                ) : null}
              </div>
            );
          })
        )}
      </div>

      {error ? (
        <p className="mx-3 mb-1 rounded-xl border border-red-900/60 bg-red-950/20 px-3 py-2 text-xs text-red-300">
          {error}
        </p>
      ) : null}

      <div className="safe-bottom border-t border-zinc-800/60 bg-zinc-950/90 px-3 py-2.5 backdrop-blur">
        <div className="flex items-end gap-2">
          <div className="glass flex min-h-11 flex-1 items-end rounded-3xl border border-zinc-800/80 pl-3.5 pr-1 py-1">
            <textarea
              className="max-h-32 min-h-9 flex-1 resize-none bg-transparent py-1.5 text-[15px] text-zinc-100 placeholder-zinc-600 focus:outline-none"
              placeholder="Message the agent…"
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            {anyActive ? (
              <button
                className="mb-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-red-700/90 text-white transition-transform active:scale-95"
                onClick={() => void doStop()}
                aria-label="Stop"
              >
                <StopIcon className="h-4 w-4" />
              </button>
            ) : (
              <button
                className="mb-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-gradient-to-b from-emerald-500 to-emerald-600 text-white shadow-lg shadow-emerald-950/40 transition-transform active:scale-95 disabled:opacity-40 disabled:shadow-none"
                disabled={busy || !input.trim()}
                onClick={() => void send()}
                aria-label="Send"
              >
                {busy ? <Spinner className="h-4 w-4" /> : <SendIcon className="h-4 w-4" />}
              </button>
            )}
          </div>
        </div>
        <div className="mt-1.5 flex items-center justify-between px-1.5">
          <button
            onClick={() => setModelOpen(true)}
            className="flex items-center gap-1 rounded-full border border-zinc-800 bg-zinc-900/60 px-2.5 py-1 text-[11px] text-zinc-400 transition-colors active:text-zinc-200"
          >
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500/80" />
            {prettyModel(model)}
            <ChevronIcon className="h-3 w-3 -rotate-90 text-zinc-600" />
          </button>
          <span className="text-[10px] text-zinc-600">Enter to send</span>
        </div>
      </div>

      {modelOpen ? (
        <ModelSheet
          models={models}
          value={model}
          onSelect={setModel}
          onClose={() => setModelOpen(false)}
        />
      ) : null}
    </div>
  );
}
