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
import EventCard from "../components/EventCard";
import Spinner from "../components/Spinner";
import Markdown from "../components/Markdown";
import { ChatIcon, SendIcon, StopIcon } from "../components/Icons";
import { inputBase, textareaBase } from "../lib/ui";

const TERMINAL: TaskStatus[] = ["done", "failed", "stopped"];
const FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-2.5-pro"];

const str = (v: unknown, fallback = ""): string => (typeof v === "string" ? v : fallback);

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-emerald-600 px-3.5 py-2.5 text-sm leading-relaxed text-white">
        {text}
      </div>
    </div>
  );
}

function AssistantBubble({ text }: { text: string }) {
  if (!text.trim()) return null;
  return (
    <div className="rounded-2xl rounded-tl-sm border border-zinc-800 border-l-2 border-l-emerald-600/70 bg-zinc-900/60 px-3.5 py-2.5">
      <Markdown text={text} />
    </div>
  );
}

function StreamingBubble({ text }: { text: string }) {
  return (
    <div className="rounded-2xl rounded-tl-sm border border-zinc-800 border-l-2 border-l-emerald-600/70 bg-zinc-900/60 px-3.5 py-2.5">
      <Markdown text={text} />
      <span className="ml-0.5 inline-block h-4 w-1.5 translate-y-0.5 animate-pulse rounded-sm bg-emerald-400" />
    </div>
  );
}

function statusLabel(status: TaskStatus): string {
  switch (status) {
    case "queued":
      return "Queued…";
    case "running":
      return "Working…";
    case "awaiting_approval":
      return "Waiting for approval…";
    default:
      return "";
  }
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
    const iv = window.setInterval(() => {
      void refreshTasks().catch(() => undefined);
    }, 4000);
    return () => window.clearInterval(iv);
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
    <div className="safe-top flex min-h-dvh flex-col px-4 pt-4">
      <div className="mb-3 flex items-center gap-2">
        <Link to="/" className="text-zinc-500" aria-label="Back">
          ←
        </Link>
        <ChatIcon className="h-5 w-5 text-emerald-400" />
        <h1 className="min-w-0 flex-1 truncate text-base font-semibold text-zinc-100">
          {project.name}
        </h1>
        {anyActive ? (
          <span className="flex items-center gap-1.5 text-[11px] text-zinc-500">
            <span className="h-2 w-2 rounded-full bg-emerald-400 pulse-dot" />
            working
          </span>
        ) : null}
      </div>

      {error ? (
        <p className="mb-2 rounded-lg border border-red-900/60 bg-red-950/20 px-3 py-2 text-xs text-red-300">
          {error}
        </p>
      ) : null}

      <div
        ref={feedRef}
        onScroll={onScroll}
        className="flex flex-1 flex-col gap-3 overflow-y-auto pb-4"
        style={{ maxHeight: "calc(100dvh - 14rem)" }}
      >
        {tasks.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-16 text-center">
            <ChatIcon className="h-8 w-8 text-zinc-700" />
            <p className="text-sm text-zinc-400">Ask me anything</p>
            <p className="max-w-xs text-xs text-zinc-600">
              I can build files, search the web, read and send email, use GitHub, and more.
            </p>
          </div>
        ) : (
          tasks.map((task) => {
            const events = (eventsByTask[task.id] ?? []).filter((e) => e.type !== "checkpoint");
            const hasCompleted = events.some((e) => e.type === "task_completed");
            const active = !TERMINAL.includes(task.status);
            return (
              <div key={task.id} className="flex flex-col gap-2.5">
                <UserBubble text={task.prompt} />
                {events.map((event) => (
                  <EventCard
                    key={event.seq}
                    event={event}
                    decidedApprovals={decided}
                    onDecided={() => undefined}
                  />
                ))}
                {streamText[task.id] ? <StreamingBubble text={streamText[task.id]} /> : null}
                {active ? (
                  <div className="flex items-center gap-2 pl-1 text-xs text-zinc-500">
                    <Spinner className="h-3.5 w-3.5" /> {statusLabel(task.status)}
                  </div>
                ) : task.status === "done" ? (
                  !hasCompleted ? <AssistantBubble text={task.result_summary || ""} /> : null
                ) : task.status === "failed" ? (
                  !events.some((e) => e.type === "task_failed") ? (
                    <div className="rounded-xl border border-red-800/70 bg-red-950/25 px-3.5 py-3">
                      <p className="text-xs font-semibold uppercase tracking-wide text-red-300">
                        Failed
                      </p>
                      <p className="mt-1 whitespace-pre-wrap text-sm text-zinc-300">
                        {task.error || "the agent hit an error"}
                      </p>
                    </div>
                  ) : null
                ) : task.status === "stopped" ? (
                  !events.some((e) => e.type === "task_stopped") ? (
                    <div className="rounded-xl border border-zinc-700 bg-zinc-900/60 px-3.5 py-2.5">
                      <p className="text-xs text-zinc-400">Stopped</p>
                    </div>
                  ) : null
                ) : null}
              </div>
            );
          })
        )}
      </div>

      <div className="safe-bottom sticky bottom-0 -mx-4 mt-auto border-t border-zinc-800 bg-zinc-950/95 px-4 py-3 backdrop-blur">
        <div className="mb-2">
          <select
            className={`${inputBase} h-9 text-xs`}
            value={model}
            onChange={(e) => setModel(e.target.value)}
          >
            {models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-end gap-2">
          <textarea
            className={`${textareaBase} max-h-32 min-h-11 flex-1 resize-none`}
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
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-red-700 text-white active:bg-red-800"
              onClick={() => void doStop()}
              aria-label="Stop"
            >
              <StopIcon />
            </button>
          ) : (
            <button
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-emerald-600 text-white active:bg-emerald-700 disabled:opacity-50"
              disabled={busy || !input.trim()}
              onClick={() => void send()}
              aria-label="Send"
            >
              {busy ? <Spinner className="h-4 w-4" /> : <SendIcon />}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
