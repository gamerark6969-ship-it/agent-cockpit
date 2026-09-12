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
import { streamTaskEvents, type StreamHandle, type StreamStatus } from "../lib/sse";
import EventCard from "../components/EventCard";
import Spinner from "../components/Spinner";
import StatusBadge from "../components/StatusBadge";
import { ChatIcon, SendIcon, StopIcon } from "../components/Icons";
import { inputBase, textareaBase } from "../lib/ui";

const TERMINAL: TaskStatus[] = ["done", "failed", "stopped"];
const FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-2.5-pro", "gpt-4.1"];

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
      <p className="whitespace-pre-wrap text-sm leading-relaxed text-zinc-200">{text}</p>
    </div>
  );
}

export default function Chat() {
  const { id = "" } = useParams();
  const [project, setProject] = useState<Project | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [liveTaskId, setLiveTaskId] = useState<string | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [streamStatus, setStreamStatus] = useState<StreamStatus>("closed");

  const seen = useRef<Set<number>>(new Set());
  const handleRef = useRef<StreamHandle | null>(null);
  const feedRef = useRef<HTMLDivElement | null>(null);
  const stick = useRef(true);

  const closeStream = () => {
    handleRef.current?.close();
    handleRef.current = null;
    setStreamStatus("closed");
  };

  const loadTasks = async () => {
    const rows = await listTasks(id);
    setTasks([...rows].sort((a, b) => a.created_at.localeCompare(b.created_at)));
    return rows;
  };

  useEffect(() => {
    let alive = true;
    setLoading(true);
    void (async () => {
      try {
        const [p, rows] = await Promise.all([getProject(id), listTasks(id)]);
        if (!alive) return;
        setProject(p);
        setTasks([...rows].sort((a, b) => a.created_at.localeCompare(b.created_at)));
        const active = [...rows]
          .reverse()
          .find((t) => !TERMINAL.includes(t.status));
        if (active) void openStream(active);
        const m = await listModels()
          .then((r) => r.models)
          .catch(() => []);
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
      closeStream();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  useEffect(() => {
    const el = feedRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [events, tasks, liveTaskId]);

  const openStream = async (task: Task) => {
    closeStream();
    seen.current = new Set();
    setEvents([]);
    setLiveTaskId(task.id);
    try {
      const history = await getEvents(task.id, 0);
      ingest(history);
      const last = history.length ? history[history.length - 1].seq : 0;
      if (TERMINAL.includes(task.status)) {
        setLiveTaskId(null);
        return;
      }
      handleRef.current = streamTaskEvents(task.id, {
        after: last,
        onEvent: (e) => {
          ingest([e]);
          applyEvent(e);
        },
        onStatus: setStreamStatus,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to load messages");
    }
  };

  const ingest = (incoming: AgentEvent[]) => {
    const fresh = incoming.filter((e) => !seen.current.has(e.seq));
    if (!fresh.length) return;
    fresh.forEach((e) => seen.current.add(e.seq));
    setEvents((prev) => [...prev, ...fresh].sort((a, b) => a.seq - b.seq));
  };

  const applyEvent = (e: AgentEvent) => {
    if (e.type === "task_completed") {
      setTasks((prev) =>
        prev.map((t) =>
          t.id === e.task_id
            ? {
                ...t,
                status: "done",
                result_summary: String(e.payload.result_summary ?? t.result_summary ?? ""),
              }
            : t,
        ),
      );
    } else if (e.type === "task_failed") {
      setTasks((prev) =>
        prev.map((t) =>
          t.id === e.task_id
            ? { ...t, status: "failed", error: String(e.payload.error ?? "") }
            : t,
        ),
      );
    } else if (e.type === "task_stopped") {
      setTasks((prev) =>
        prev.map((t) => (t.id === e.task_id ? { ...t, status: "stopped" } : t)),
      );
    }
    if (e.type === "task_completed" || e.type === "task_failed" || e.type === "task_stopped") {
      closeStream();
      setLiveTaskId(null);
      void loadTasks().catch(() => undefined);
    }
  };

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
      await openStream(task);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to send message");
    } finally {
      setBusy(false);
    }
  };

  const doStop = async () => {
    if (!liveTaskId) return;
    try {
      await stopTask(liveTaskId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to stop");
    }
  };

  const decided = useMemo(() => {
    const map: Record<string, "approved" | "denied"> = {};
    for (const e of events) {
      if (e.type !== "approval_decision") continue;
      const aid = String(e.payload.approval_id ?? "");
      const decision = String(e.payload.decision ?? "");
      if (aid && (decision === "approve" || decision === "deny")) {
        map[aid] = decision === "approve" ? "approved" : "denied";
      }
    }
    return map;
  }, [events]);

  const visibleEvents = useMemo(() => events.filter((e) => e.type !== "checkpoint"), [events]);

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

  const live = streamStatus === "connected";

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
        {liveTaskId ? (
          <span className="flex items-center gap-1.5 text-[11px] text-zinc-500">
            <span
              className={`h-2 w-2 rounded-full ${live ? "bg-emerald-400" : "bg-amber-500"} pulse-dot`}
            />
            {live ? "working" : streamStatus}
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
              I can search the web, read and send email, check GitHub, and more.
            </p>
          </div>
        ) : (
          tasks.map((task) => {
            const isLive = task.id === liveTaskId;
            return (
              <div key={task.id} className="flex flex-col gap-2.5">
                <UserBubble text={task.prompt} />
                {isLive ? (
                  visibleEvents.length === 0 ? (
                    <div className="flex items-center gap-2 pl-1 text-xs text-zinc-500">
                      <Spinner className="h-3.5 w-3.5" /> thinking…
                    </div>
                  ) : (
                    visibleEvents.map((event) => (
                      <EventCard
                        key={event.seq}
                        event={event}
                        decidedApprovals={decided}
                        onDecided={() => undefined}
                      />
                    ))
                  )
                ) : task.status === "done" ? (
                  <AssistantBubble text={task.result_summary || ""} />
                ) : task.status === "failed" ? (
                  <div className="rounded-xl border border-red-800/70 bg-red-950/25 px-3.5 py-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-red-300">
                      Failed
                    </p>
                    <p className="mt-1 whitespace-pre-wrap text-sm text-zinc-300">
                      {task.error || "the agent hit an error"}
                    </p>
                  </div>
                ) : task.status === "stopped" ? (
                  <div className="flex items-center justify-between rounded-xl border border-zinc-700 bg-zinc-900/60 px-3.5 py-2.5">
                    <p className="text-xs text-zinc-400">Stopped</p>
                    <StatusBadge status={task.status} compact />
                  </div>
                ) : (
                  <div className="flex items-center gap-2 pl-1 text-xs text-zinc-500">
                    <Spinner className="h-3.5 w-3.5" /> queued…
                  </div>
                )}
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
          {liveTaskId ? (
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
