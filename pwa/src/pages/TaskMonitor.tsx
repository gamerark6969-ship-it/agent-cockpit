import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  getDiff,
  getEvents,
  getScreenshots,
  getTask,
  steerTask,
  stopTask,
  type AgentEvent,
  type DiffResult,
  type Screenshot,
  type Task,
  type TaskStatus,
} from "../lib/api";
import { streamTaskEvents, type StreamHandle, type StreamStatus } from "../lib/sse";
import EventCard from "../components/EventCard";
import Spinner from "../components/Spinner";
import StatusBadge from "../components/StatusBadge";
import EmptyState from "../components/EmptyState";
import { GitPrIcon, SendIcon, StopIcon } from "../components/Icons";
import { elapsed, formatNumber } from "../lib/utils";
import { inputBase } from "../lib/ui";
import AuthedImage from "../components/AuthedImage";

const TERMINAL: TaskStatus[] = ["done", "failed", "stopped"];

type Tab = "feed" | "diff" | "screenshots";

export default function TaskMonitor() {
  const { id = "" } = useParams();
  const [task, setTask] = useState<Task | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [streamStatus, setStreamStatus] = useState<StreamStatus>("closed");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<Tab>("feed");
  const [steer, setSteer] = useState("");
  const [busy, setBusy] = useState(false);
  const [diff, setDiff] = useState<DiffResult | null>(null);
  const [shots, setShots] = useState<Screenshot[]>([]);
  const [, setTick] = useState(0);

  const seen = useRef<Set<number>>(new Set());
  const handleRef = useRef<StreamHandle | null>(null);
  const feedRef = useRef<HTMLDivElement | null>(null);
  const stick = useRef(true);

  const ingest = (incoming: AgentEvent[]) => {
    const fresh = incoming.filter((e) => !seen.current.has(e.seq));
    if (!fresh.length) return;
    fresh.forEach((e) => seen.current.add(e.seq));
    setEvents((prev) => [...prev, ...fresh].sort((a, b) => a.seq - b.seq));
  };

  const applyEvent = (e: AgentEvent) => {
    if (e.type === "task_completed") {
      setTask((t) =>
        t
          ? {
              ...t,
              status: "done",
              result_summary: String(e.payload.result_summary ?? t.result_summary ?? ""),
            }
          : t,
      );
    } else if (e.type === "task_failed") {
      setTask((t) => (t ? { ...t, status: "failed", error: String(e.payload.error ?? "") } : t));
    } else if (e.type === "task_stopped") {
      setTask((t) => (t ? { ...t, status: "stopped" } : t));
    } else if (e.type === "checkpoint") {
      setTask((t) =>
        t ? { ...t, iterations: Number(e.payload.iteration ?? t.iterations) } : t,
      );
    }
    if (e.type === "task_completed" || e.type === "task_failed") {
      handleRef.current?.close();
      handleRef.current = null;
    }
  };

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setEvents([]);
    setTask(null);
    seen.current = new Set();
    void (async () => {
      try {
        const t = await getTask(id);
        if (!alive) return;
        setTask(t);
        const history = await getEvents(id, 0);
        if (!alive) return;
        ingest(history);
        const last = history.length ? history[history.length - 1].seq : 0;
        if (!TERMINAL.includes(t.status)) {
          handleRef.current = streamTaskEvents(id, {
            after: last,
            onEvent: (e) => {
              ingest([e]);
              applyEvent(e);
            },
            onStatus: (s) => {
              if (alive) setStreamStatus(s);
            },
          });
        } else {
          setStreamStatus("closed");
        }
      } catch (err) {
        if (alive) setError(err instanceof Error ? err.message : "failed to load task");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
      handleRef.current?.close();
      handleRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  useEffect(() => {
    const interval = window.setInterval(() => setTick((v) => v + 1), 1000);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    if (tab !== "feed") return;
    const el = feedRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [events, tab]);

  useEffect(() => {
    if (tab === "diff") {
      getDiff(id)
        .then(setDiff)
        .catch(() => setDiff({ summary: "", files: [] }));
    } else if (tab === "screenshots") {
      getScreenshots(id)
        .then(setShots)
        .catch(() => setShots([]));
    }
  }, [tab, id, events.length]);

  const decided = useMemo(() => {
    const map: Record<string, "approved" | "denied"> = {};
    for (const e of events) {
      if (e.type !== "approval_decision") continue;
      const approvalId = String(e.payload.approval_id ?? "");
      const decision = String(e.payload.decision ?? "");
      if (approvalId && (decision === "approve" || decision === "deny")) {
        map[approvalId] = decision === "approve" ? "approved" : "denied";
      }
    }
    return map;
  }, [events]);

  const onScroll = () => {
    const el = feedRef.current;
    if (!el) return;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  };

  const sendSteer = async () => {
    if (!steer.trim()) return;
    setBusy(true);
    setError("");
    try {
      await steerTask(id, steer.trim());
      setSteer("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to send message");
    } finally {
      setBusy(false);
    }
  };

  const doStop = async () => {
    if (!window.confirm("Stop this task? The agent will halt.")) return;
    setBusy(true);
    setError("");
    try {
      await stopTask(id);
      setTask((t) => (t ? { ...t, status: "stopped" } : t));
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to stop task");
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-16 text-zinc-500">
        <Spinner />
      </div>
    );
  }

  if (!task) {
    return (
      <div className="px-4 pt-6">
        <p className="text-sm text-red-400">{error || "task not found"}</p>
      </div>
    );
  }

  const isTerminal = TERMINAL.includes(task.status);
  const live = streamStatus === "connected";

  return (
    <div className="safe-top flex min-h-dvh flex-col px-4 pt-4">
      <Link to={`/projects/${task.project_id}`} className="mb-2 inline-block text-xs text-zinc-500">
        ← Project
      </Link>

      <div className="mb-3">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={task.status} />
          {!isTerminal ? (
            <span className="flex items-center gap-1.5 text-[11px] text-zinc-500">
              <span
                className={`h-2 w-2 rounded-full ${
                  live ? "bg-emerald-400 pulse-dot" : "bg-amber-500 pulse-dot"
                }`}
              />
              {live ? "live" : streamStatus}
            </span>
          ) : null}
        </div>
        <p className="mt-2 text-sm text-zinc-300">{task.prompt}</p>
        <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-zinc-600">
          <span>elapsed {elapsed(task.created_at)}</span>
          <span>{task.iterations} iterations</span>
          <span>{formatNumber(task.tokens_used)} tokens</span>
          {task.model ? <span>{task.model}</span> : null}
        </div>
      </div>

      {error ? (
        <p className="mb-2 rounded-lg border border-red-900/60 bg-red-950/20 px-3 py-2 text-xs text-red-300">
          {error}
        </p>
      ) : null}

      <div className="mb-3 flex rounded-xl border border-zinc-800 bg-zinc-900 p-1">
        {(["feed", "diff", "screenshots"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`flex-1 rounded-lg py-2 text-xs font-medium capitalize transition-colors ${
              tab === t ? "bg-zinc-800 text-zinc-100" : "text-zinc-500"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === "feed" ? (
        <div
          ref={feedRef}
          onScroll={onScroll}
          className="flex flex-1 flex-col gap-2.5 overflow-y-auto pb-4"
          style={{ maxHeight: "calc(100dvh - 20rem)" }}
        >
          {events.length === 0 ? (
            <EmptyState title="Waiting for the agent" description="The run will appear here." />
          ) : (
            events.map((event) => (
              <EventCard
                key={event.seq}
                event={event}
                decidedApprovals={decided}
                onDecided={() => {
                  /* decision event will arrive over SSE */
                }}
              />
            ))
          )}
        </div>
      ) : null}

      {tab === "diff" ? (
        <div className="flex-1 pb-4">
          {!diff || diff.files.length === 0 ? (
            <EmptyState title="No changes yet" description="File changes will show up as the agent works." />
          ) : (
            <div className="flex flex-col gap-2">
              {diff.summary ? (
                <p className="text-xs text-zinc-500">{diff.summary}</p>
              ) : null}
              {diff.files.map((f) => (
                <div
                  key={f.path}
                  className="flex items-center justify-between gap-3 rounded-xl border border-zinc-800 bg-zinc-900/40 px-3 py-2.5"
                >
                  <div className="min-w-0">
                    <p className="truncate font-mono text-xs text-zinc-300">{f.path}</p>
                    <p className="text-[10px] uppercase tracking-wide text-zinc-600">{f.status}</p>
                  </div>
                  <div className="flex shrink-0 gap-2 font-mono text-[11px]">
                    {f.additions > 0 ? <span className="text-emerald-400">+{f.additions}</span> : null}
                    {f.deletions > 0 ? <span className="text-red-400">-{f.deletions}</span> : null}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : null}

      {tab === "screenshots" ? (
        <div className="flex-1 pb-4">
          {shots.length === 0 ? (
            <EmptyState title="No screenshots yet" description="Browser activity will be captured here." />
          ) : (
            <div className="grid grid-cols-2 gap-2">
              {shots.map((s) => (
                <div key={s.id} className="overflow-hidden rounded-xl border border-zinc-800">
                  <AuthedImage
                    artifactId={s.id}
                    alt={s.filename}
                    className="h-32 w-full object-cover"
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      ) : null}

      {!isTerminal ? (
        <div className="safe-bottom sticky bottom-0 -mx-4 mt-auto border-t border-zinc-800 bg-zinc-950/95 px-4 py-3 backdrop-blur">
          <div className="flex items-center gap-2">
            <input
              className={inputBase}
              placeholder="Steer the agent…"
              value={steer}
              onChange={(e) => setSteer(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void sendSteer();
              }}
            />
            <button
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-emerald-600 text-white active:bg-emerald-700 disabled:opacity-50"
              disabled={busy || !steer.trim()}
              onClick={() => void sendSteer()}
              aria-label="Send"
            >
              <SendIcon />
            </button>
            <button
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-red-700 text-white active:bg-red-800 disabled:opacity-50"
              disabled={busy}
              onClick={() => void doStop()}
              aria-label="Stop"
            >
              <StopIcon />
            </button>
          </div>
        </div>
      ) : task.status === "done" ? (
        <div className="safe-bottom sticky bottom-0 -mx-4 mt-auto border-t border-zinc-800 bg-zinc-950/95 px-4 py-3">
          <div className="flex items-center gap-2 text-xs text-emerald-400">
            <GitPrIcon className="h-4 w-4" />
            Task finished. Review the pull request or start another task.
          </div>
        </div>
      ) : null}

      {busy ? (
        <div className="fixed bottom-24 right-4 rounded-full bg-zinc-900 p-2 shadow-lg">
          <Spinner className="h-4 w-4 text-emerald-400" />
        </div>
      ) : null}
    </div>
  );
}
