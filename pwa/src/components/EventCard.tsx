import { useState } from "react";
import { decideApproval, type AgentEvent } from "../lib/api";
import { prettyJson, truncate } from "../lib/utils";
import { CheckIcon, ChevronIcon, CodeIcon, GitPrIcon, GlobeIcon, WarningIcon, XIcon } from "./Icons";
import Spinner from "./Spinner";
import AuthedImage from "./AuthedImage";
import FilePreview from "./FilePreview";
import Markdown from "./Markdown";

const str = (v: unknown, fallback = ""): string => (typeof v === "string" ? v : fallback);
const num = (v: unknown, fallback = 0): number => (typeof v === "number" ? v : fallback);
const bool = (v: unknown): boolean => v === true;

function AgentBubble({ content }: { content: string }) {
  if (!content.trim()) return null;
  return (
    <div className="rounded-2xl rounded-tl-sm border border-zinc-800 border-l-2 border-l-emerald-600/70 bg-zinc-900/60 px-3.5 py-2.5">
      <Markdown text={content} />
    </div>
  );
}

function ToolCall({ tool, args }: { tool: string; args: unknown }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 px-3 py-2">
      <div className="flex items-center gap-2 text-xs">
        <CodeIcon className="h-4 w-4 text-blue-400" />
        <span className="font-mono text-blue-300">{tool}</span>
      </div>
      <details className="mt-1">
        <summary className="cursor-pointer select-none text-[11px] text-zinc-500 hover:text-zinc-400">
          arguments
        </summary>
        <pre className="mt-1 max-h-64 overflow-auto rounded-lg bg-black/50 p-2 text-[11px] leading-relaxed text-zinc-400">
          {prettyJson(args)}
        </pre>
      </details>
    </div>
  );
}

function CompactToolRow({ tool, ok, summary }: { tool: string; ok: boolean; summary: string }) {
  const [open, setOpen] = useState(false);
  const firstLine = summary.split("\n").find((l) => l.trim()) || "";
  return (
    <div className="py-0.5">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 py-1 text-left text-[11px] text-zinc-500 transition-colors active:text-zinc-300"
      >
        {ok ? (
          <CheckIcon className="h-3 w-3 shrink-0 text-emerald-500/70" />
        ) : (
          <XIcon className="h-3 w-3 shrink-0 text-red-500/70" />
        )}
        <span className="shrink-0 font-mono">{tool}</span>
        {firstLine ? <span className="min-w-0 flex-1 truncate">{firstLine}</span> : null}
        <ChevronIcon
          className={`h-3.5 w-3.5 shrink-0 transition-transform ${open ? "rotate-90" : ""}`}
        />
      </button>
      {open && summary ? (
        <pre className="mt-1 max-h-56 overflow-auto rounded-lg bg-black/50 p-2 text-[11px] leading-relaxed text-zinc-400">
          {truncate(summary, 1500)}
        </pre>
      ) : null}
    </div>
  );
}

function ToolResult({
  tool,
  ok,
  summary,
  truncated: wasTruncated,
}: {
  tool: string;
  ok: boolean;
  summary: string;
  truncated: boolean;
}) {
  return (
    <div
      className={`rounded-xl border px-3 py-2 ${
        ok ? "border-emerald-900/60 bg-emerald-950/20" : "border-red-900/60 bg-red-950/20"
      }`}
    >
      <div className="flex items-center gap-2 text-xs">
        {ok ? (
          <CheckIcon className="h-3.5 w-3.5 text-emerald-400" />
        ) : (
          <XIcon className="h-3.5 w-3.5 text-red-400" />
        )}
        <span className="font-mono text-zinc-300">{tool}</span>
        {wasTruncated ? <span className="text-[10px] text-zinc-500">truncated</span> : null}
      </div>
      {summary ? (
        <p className="mt-1 whitespace-pre-wrap break-words text-[12px] leading-relaxed text-zinc-400">
          {truncate(summary, 1500)}
        </p>
      ) : null}
    </div>
  );
}

function TerminalBlock({
  command,
  exitCode,
  output,
  startCollapsed = false,
}: {
  command: string;
  exitCode: number;
  output: string;
  startCollapsed?: boolean;
}) {
  const [expanded, setExpanded] = useState(!startCollapsed);
  const lines = output.length ? output.split("\n") : [];
  const collapsible = lines.length > 10;
  const shown = collapsible && !expanded ? lines.slice(0, 10).join("\n") : output;
  const ok = exitCode === 0;
  return (
    <div className="overflow-hidden rounded-xl border border-zinc-800/80 bg-black/60">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 border-b border-zinc-800/60 bg-zinc-900/40 px-3 py-1.5 text-left"
      >
        <span className="font-mono text-[11px] text-zinc-500">$</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-zinc-300">
          {command}
        </span>
        <span
          className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
            ok ? "bg-emerald-950 text-emerald-400" : "bg-red-950 text-red-400"
          }`}
        >
          {exitCode}
        </span>
        <ChevronIcon
          className={`h-3.5 w-3.5 shrink-0 text-zinc-600 transition-transform ${expanded ? "rotate-90" : ""}`}
        />
      </button>
      {expanded && output ? (
        <pre className="max-h-72 overflow-auto px-3 py-2 text-[11px] leading-relaxed text-zinc-400">
          {shown}
        </pre>
      ) : null}
    </div>
  );
}

function ScreenshotBlock({ artifactId, filename }: { artifactId: string; filename: string }) {
  const [full, setFull] = useState(false);
  return (
    <>
      <div className="overflow-hidden rounded-xl border border-zinc-800">
        <AuthedImage
          artifactId={artifactId}
          alt={filename}
          className="max-h-72 w-full object-cover"
          onOpen={() => setFull(true)}
        />
      </div>
      {full ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/90 p-4"
          onClick={() => setFull(false)}
        >
          <AuthedImage
            artifactId={artifactId}
            alt={filename}
            className="max-h-full max-w-full object-contain"
          />
        </div>
      ) : null}
    </>
  );
}

function ApprovalCard({
  approvalId,
  kind,
  description,
  payload,
  decided,
  onDecided,
}: {
  approvalId: string;
  kind: string;
  description: string;
  payload: unknown;
  decided?: "approved" | "denied";
  onDecided?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const decide = async (decision: "approve" | "deny") => {
    setBusy(true);
    setError("");
    try {
      await decideApproval(approvalId, decision);
      onDecided?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to submit decision");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border border-amber-800/70 bg-amber-950/25 px-3.5 py-3">
      <div className="flex items-center gap-2">
        <WarningIcon className="h-4 w-4 text-amber-400" />
        <span className="text-xs font-semibold uppercase tracking-wide text-amber-300">
          Approval required
        </span>
        <span className="rounded bg-amber-900/50 px-1.5 py-0.5 font-mono text-[10px] text-amber-300">
          {kind}
        </span>
      </div>
      <p className="mt-1.5 text-sm text-zinc-200">{description}</p>
      <details className="mt-1">
        <summary className="cursor-pointer text-[11px] text-amber-500/80">details</summary>
        <pre className="mt-1 max-h-56 overflow-auto rounded-lg bg-black/50 p-2 text-[11px] text-zinc-400">
          {prettyJson(payload)}
        </pre>
      </details>
      {decided ? (
        <p
          className={`mt-2 text-xs font-medium ${
            decided === "approved" ? "text-emerald-400" : "text-red-400"
          }`}
        >
          {decided === "approved" ? "Approved" : "Denied"}
        </p>
      ) : (
        <div className="mt-3 flex gap-2">
          <button
            disabled={busy}
            onClick={() => decide("approve")}
            className="flex h-10 flex-1 items-center justify-center gap-1.5 rounded-lg bg-emerald-600 text-sm font-semibold text-white active:bg-emerald-700 disabled:opacity-50"
          >
            {busy ? <Spinner className="h-4 w-4" /> : <CheckIcon className="h-4 w-4" />}
            Approve
          </button>
          <button
            disabled={busy}
            onClick={() => decide("deny")}
            className="flex h-10 flex-1 items-center justify-center gap-1.5 rounded-lg bg-red-700 text-sm font-semibold text-white active:bg-red-800 disabled:opacity-50"
          >
            <XIcon className="h-4 w-4" />
            Deny
          </button>
        </div>
      )}
      {error ? <p className="mt-2 text-xs text-red-400">{error}</p> : null}
    </div>
  );
}

function TaskCompleted({
  resultSummary,
  prUrl,
  prNumber,
  branch,
  iterations,
  tokensUsed,
  compact = false,
}: {
  resultSummary: string;
  prUrl: string;
  prNumber: number | null;
  branch: string;
  iterations: number;
  tokensUsed: number;
  compact?: boolean;
}) {
  if (compact && !prUrl) {
    // Chat feed: the summary is the answer; skip the chrome unless there's a PR.
    return resultSummary ? (
      <div className="rounded-2xl rounded-tl-sm border border-zinc-800 border-l-2 border-l-emerald-600/70 bg-zinc-900/60 px-3.5 py-2.5">
        <Markdown text={resultSummary} />
      </div>
    ) : null;
  }
  return (
    <div className="rounded-xl border border-emerald-800/70 bg-emerald-950/25 px-3.5 py-3">
      <div className="flex items-center gap-2">
        <CheckIcon className="h-4 w-4 text-emerald-400" />
        <span className="text-xs font-semibold uppercase tracking-wide text-emerald-300">
          Task completed
        </span>
      </div>
      {resultSummary ? (
        <p className="mt-1.5 whitespace-pre-wrap text-sm text-zinc-200">{resultSummary}</p>
      ) : null}
      <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-zinc-400">
        {branch ? <span>branch {branch}</span> : null}
        <span>{iterations} iterations</span>
        <span>{tokensUsed.toLocaleString()} tokens</span>
      </div>
      {prUrl ? (
        <a
          href={prUrl}
          target="_blank"
          rel="noreferrer"
          className="mt-3 flex h-10 items-center justify-center gap-2 rounded-lg bg-emerald-600 text-sm font-semibold text-white active:bg-emerald-700"
        >
          <GitPrIcon className="h-4 w-4" />
          Open pull request{prNumber ? ` #${prNumber}` : ""}
        </a>
      ) : null}
    </div>
  );
}

export interface EventCardProps {
  event: AgentEvent;
  /** Chat-feed mode: hides raw tool_call noise, collapses terminal output. */
  compact?: boolean;
  decidedApprovals?: Record<string, "approved" | "denied">;
  onDecided?: () => void;
}

export default function EventCard({
  event,
  compact = false,
  decidedApprovals = {},
  onDecided,
}: EventCardProps) {
  const p = event.payload || {};
  switch (event.type) {
    case "model_switch":
      return (
        <div className="flex justify-center py-0.5">
          <span className="rounded-full border border-zinc-800/80 bg-zinc-900/50 px-2.5 py-0.5 text-[10px] text-zinc-500">
            {str(p.from)} busy · switched to {str(p.to)}
          </span>
        </div>
      );
    case "agent_delta":
      return null;
    case "agent_message":
      return <AgentBubble content={str(p.content)} />;
    case "tool_call":
      if (compact) return null;
      return <ToolCall tool={str(p.tool, "tool")} args={p.args ?? {}} />;
    case "tool_result":
      if (compact) {
        if (str(p.tool) === "bash" || str(p.tool) === "finish") return null;
        return (
          <CompactToolRow tool={str(p.tool, "tool")} ok={bool(p.ok)} summary={str(p.summary)} />
        );
      }
      return (
        <ToolResult
          tool={str(p.tool, "tool")}
          ok={bool(p.ok)}
          summary={str(p.summary)}
          truncated={bool(p.truncated)}
        />
      );
    case "terminal":
      return (
        <TerminalBlock
          command={str(p.command)}
          exitCode={num(p.exit_code, -1)}
          output={str(p.output)}
          startCollapsed={compact}
        />
      );
    case "screenshot":
      return (
        <ScreenshotBlock
          artifactId={str(p.artifact_id)}
          filename={str(p.filename, "screenshot.png")}
        />
      );
    case "file":
      return (
        <FilePreview
          artifactId={str(p.artifact_id)}
          filename={str(p.filename, "file")}
          mime={str(p.mime)}
          title={str(p.title)}
        />
      );
    case "deployed": {
      const live = str(p.url);
      const persistent = str(p.persistent_url);
      const backup = str(p.artifact_url);
      const title = str(p.title);
      return (
        <div className="rounded-2xl border border-emerald-800/70 bg-emerald-950/20 px-3.5 py-3">
          <div className="flex items-center gap-2">
            <GlobeIcon className="h-4 w-4 text-emerald-400" />
            <span className="text-xs font-semibold uppercase tracking-wide text-emerald-300">
              Site deployed
            </span>
          </div>
          {title ? <p className="mt-1 text-sm text-zinc-200">{title}</p> : null}
          <div className="mt-2 flex flex-col gap-2">
            {persistent ? (
              <a
                href={persistent}
                target="_blank"
                rel="noreferrer"
                className="flex h-10 items-center justify-center gap-2 rounded-lg bg-emerald-600 text-sm font-semibold text-white active:bg-emerald-700"
              >
                <GlobeIcon className="h-4 w-4" /> Open site
              </a>
            ) : live ? (
              <a
                href={live}
                target="_blank"
                rel="noreferrer"
                className="flex h-10 items-center justify-center gap-2 rounded-lg bg-emerald-600 text-sm font-semibold text-white active:bg-emerald-700"
              >
                <GlobeIcon className="h-4 w-4" /> Open live preview
              </a>
            ) : backup ? (
              <a
                href={backup}
                target="_blank"
                rel="noreferrer"
                className="flex h-10 items-center justify-center gap-2 rounded-lg bg-emerald-600 text-sm font-semibold text-white active:bg-emerald-700"
              >
                <GlobeIcon className="h-4 w-4" /> Open page
              </a>
            ) : null}
            {persistent && live ? (
              <a href={live} target="_blank" rel="noreferrer" className="text-center text-[11px] text-emerald-400/80">
                live preview (temporary)
              </a>
            ) : null}
          </div>
        </div>
      );
    }
    case "steered":
      return (
        <div className="flex justify-end">
          <span className="rounded-full border border-emerald-900/70 bg-emerald-950/30 px-2.5 py-0.5 text-[11px] text-emerald-300/90">
            steered: {str(p.message)}
          </span>
        </div>
      );
    case "context_compacted":
      return (
        <div className="flex justify-center py-0.5">
          <span className="rounded-full border border-zinc-800/80 bg-zinc-900/50 px-2.5 py-0.5 text-[10px] text-zinc-500">
            context compacted
          </span>
        </div>
      );
    case "approval_request":
      return (
        <ApprovalCard
          approvalId={str(p.approval_id)}
          kind={str(p.kind, "action")}
          description={str(p.description, "The agent needs approval to continue.")}
          payload={p.payload ?? {}}
          decided={decidedApprovals[str(p.approval_id)]}
          onDecided={onDecided}
        />
      );
    case "approval_decision":
      return (
        <div className="flex justify-center">
          <span className="rounded-full border border-zinc-800 bg-zinc-900 px-2.5 py-0.5 text-[11px] text-zinc-500">
            approval {str(p.decision, "decided")}
          </span>
        </div>
      );
    case "task_completed":
      return (
        <TaskCompleted
          resultSummary={str(p.result_summary)}
          prUrl={str(p.pr_url)}
          prNumber={typeof p.pr_number === "number" ? p.pr_number : null}
          branch={str(p.branch)}
          iterations={num(p.iterations)}
          tokensUsed={num(p.tokens_used)}
          compact={compact}
        />
      );
    case "task_failed":
      return (
        <div className="rounded-xl border border-red-800/70 bg-red-950/25 px-3.5 py-3">
          <div className="flex items-center gap-2">
            <XIcon className="h-4 w-4 text-red-400" />
            <span className="text-xs font-semibold uppercase tracking-wide text-red-300">
              Task failed
            </span>
          </div>
          <p className="mt-1.5 whitespace-pre-wrap text-sm text-zinc-300">{str(p.error)}</p>
        </div>
      );
    case "task_stopped":
      return (
        <div className="rounded-xl border border-zinc-700 bg-zinc-900/60 px-3.5 py-2.5">
          <p className="text-xs text-zinc-400">Stopped — {str(p.reason, "no reason given")}</p>
        </div>
      );
    case "checkpoint":
      return (
        <div className="flex items-center gap-3 py-1">
          <span className="h-px flex-1 bg-zinc-800" />
          <span className="text-[10px] uppercase tracking-wider text-zinc-600">
            iteration {num(p.iteration)}
          </span>
          <span className="h-px flex-1 bg-zinc-800" />
        </div>
      );
    case "error":
      return (
        <div className="rounded-xl border border-red-900/60 bg-red-950/20 px-3 py-2">
          <p className="text-xs text-red-300">{str(p.message, "unknown error")}</p>
        </div>
      );
    case "task_started":
      return (
        <div className="flex items-center gap-3 py-1">
          <span className="h-px flex-1 bg-zinc-800" />
          <span className="text-[10px] uppercase tracking-wider text-zinc-600">
            agent started · {str(p.model, "model")}
          </span>
          <span className="h-px flex-1 bg-zinc-800" />
        </div>
      );
    default:
      return (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 px-3 py-2">
          <p className="font-mono text-[11px] text-zinc-500">{event.type}</p>
        </div>
      );
  }
}

const WORK_NOISE = new Set([
  "tool_call",
  "tool_result",
  "terminal",
  "checkpoint",
  "model_switch",
  "context_compacted",
]);

export type FeedRow =
  | { kind: "event"; event: AgentEvent }
  | { kind: "work"; key: number; events: AgentEvent[] };

/** Collapse consecutive low-level steps into a single work-log group. */
export function groupFeed(events: AgentEvent[]): FeedRow[] {
  const visible = events.filter(
    (e) =>
      e.type !== "tool_call" &&
      !(e.type === "tool_result" && (str(e.payload.tool) === "bash" || str(e.payload.tool) === "finish")),
  );
  const rows: FeedRow[] = [];
  let buf: AgentEvent[] = [];
  const flush = () => {
    if (buf.length) rows.push({ kind: "work", key: buf[0].seq, events: buf });
    buf = [];
  };
  for (const e of visible) {
    if (WORK_NOISE.has(e.type)) {
      buf.push(e);
    } else {
      flush();
      rows.push({ kind: "event", event: e });
    }
  }
  flush();
  return rows;
}

function workLabel(e: AgentEvent | undefined): string {
  if (!e) return "";
  const p = e.payload || {};
  if (e.type === "tool_result") {
    const first = str(p.summary).split("\n").find((l) => l.trim()) || "";
    return first ? `${str(p.tool, "tool")} · ${first}` : str(p.tool, "tool");
  }
  if (e.type === "terminal") return str(p.command).slice(0, 70);
  if (e.type === "checkpoint") return `iteration ${num(p.iteration)}`;
  if (e.type === "model_switch") return `switched to ${str(p.to)}`;
  if (e.type === "context_compacted") return "context compacted";
  return e.type;
}

export function WorkLog({
  events,
  decidedApprovals,
  onDecided,
}: {
  events: AgentEvent[];
  decidedApprovals?: Record<string, "approved" | "denied">;
  onDecided?: () => void;
}) {
  const [expanded, setExpanded] = useState(events.length <= 2);
  const tail = events[events.length - 1];
  return (
    <div className="rounded-2xl border border-zinc-800/70 bg-zinc-900/30 px-2 py-1.5">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 px-1 py-1 text-left text-[11px] text-zinc-500 active:text-zinc-300"
      >
        {!expanded ? (
          <span className="pulse-dot h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500/70" />
        ) : null}
        <span className="shrink-0 font-medium text-zinc-400">
          {events.length} step{events.length === 1 ? "" : "s"}
        </span>
        {!expanded && tail ? (
          <span className="min-w-0 flex-1 truncate">{workLabel(tail)}</span>
        ) : (
          <span className="flex-1" />
        )}
        <ChevronIcon
          className={`h-3.5 w-3.5 shrink-0 transition-transform ${expanded ? "rotate-90" : ""}`}
        />
      </button>
      {expanded ? (
        <div className="flex flex-col gap-1.5 px-0.5 pb-1">
          {events.map((e) => (
            <EventCard
              key={e.seq}
              event={e}
              compact
              decidedApprovals={decidedApprovals}
              onDecided={onDecided}
            />
          ))}
        </div>
      ) : tail ? (
        <div className="px-0.5 pb-1">
          <EventCard event={tail} compact decidedApprovals={decidedApprovals} onDecided={onDecided} />
        </div>
      ) : null}
    </div>
  );
}
