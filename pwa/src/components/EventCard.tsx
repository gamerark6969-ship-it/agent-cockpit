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
    <div className="border-l border-line-strong pl-3.5">
      <Markdown text={content} />
    </div>
  );
}

function ToolCall({ tool, args }: { tool: string; args: unknown }) {
  return (
    <div className="rounded-xl border border-line bg-surface px-3 py-2">
      <div className="flex items-center gap-2 text-xs">
        <CodeIcon className="h-4 w-4 text-muted" />
        <span className="font-mono text-muted">{tool}</span>
      </div>
      <details className="mt-1">
        <summary className="cursor-pointer select-none text-[11px] text-faint hover:text-muted">
          arguments
        </summary>
        <pre className="mt-1 max-h-64 overflow-auto rounded-lg bg-black/50 p-2 text-[11px] leading-relaxed text-muted">
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
        className="flex w-full items-center gap-2 py-1 text-left text-[11px] text-faint transition-colors active:text-muted"
      >
        {ok ? (
          <CheckIcon className="h-3 w-3 shrink-0 text-add" />
        ) : (
          <XIcon className="h-3 w-3 shrink-0 text-del" />
        )}
        <span className="shrink-0 font-mono">{tool}</span>
        {firstLine ? <span className="min-w-0 flex-1 truncate">{firstLine}</span> : null}
        <ChevronIcon
          className={`h-3.5 w-3.5 shrink-0 transition-transform ${open ? "rotate-90" : ""}`}
        />
      </button>
      {open && summary ? (
        <pre className="mt-1 max-h-56 overflow-auto rounded-lg bg-black/50 p-2 text-[11px] leading-relaxed text-muted">
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
        ok ? "border-line bg-surface" : "border-red-950 bg-red-950/25"
      }`}
    >
      <div className="flex items-center gap-2 text-xs">
        {ok ? (
          <CheckIcon className="h-3.5 w-3.5 text-add" />
        ) : (
          <XIcon className="h-3.5 w-3.5 text-del" />
        )}
        <span className="font-mono text-fg">{tool}</span>
        {wasTruncated ? <span className="text-[10px] text-faint">truncated</span> : null}
      </div>
      {summary ? (
        <p className="mt-1 whitespace-pre-wrap break-words text-[12px] leading-relaxed text-muted">
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
    <div className="overflow-hidden rounded-xl border border-line bg-black">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 bg-surface px-3 py-1.5 text-left"
      >
        <span className="font-mono text-[11px] text-faint">$</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-fg">{command}</span>
        <span
          className={`rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold ${
            ok ? "bg-elevated text-muted" : "bg-red-950 text-del"
          }`}
        >
          {exitCode}
        </span>
        <ChevronIcon
          className={`h-3.5 w-3.5 shrink-0 text-faint transition-transform ${expanded ? "rotate-90" : ""}`}
        />
      </button>
      {expanded && output ? (
        <pre className="max-h-72 overflow-auto px-3 py-2 text-[11px] leading-relaxed text-muted">
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
      <div className="overflow-hidden rounded-xl border border-line">
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
    <div className="rounded-xl border border-warn/35 bg-warn/10 px-3.5 py-3">
      <div className="flex items-center gap-2">
        <WarningIcon className="h-4 w-4 text-warn" />
        <span className="text-xs font-semibold uppercase tracking-wide text-warn">
          Approval required
        </span>
        <span className="rounded bg-warn/15 px-1.5 py-0.5 font-mono text-[10px] text-warn">
          {kind}
        </span>
      </div>
      <p className="mt-1.5 text-sm text-fg">{description}</p>
      <details className="mt-1">
        <summary className="cursor-pointer text-[11px] text-warn/80">details</summary>
        <pre className="mt-1 max-h-56 overflow-auto rounded-lg bg-black/50 p-2 text-[11px] text-muted">
          {prettyJson(payload)}
        </pre>
      </details>
      {decided ? (
        <p className={`mt-2 text-xs font-medium ${decided === "approved" ? "text-add" : "text-del"}`}>
          {decided === "approved" ? "Approved" : "Denied"}
        </p>
      ) : (
        <div className="mt-3 flex gap-2">
          <button
            disabled={busy}
            onClick={() => decide("approve")}
            className="flex h-10 flex-1 items-center justify-center gap-1.5 rounded-lg bg-accent text-sm font-semibold text-canvas active:bg-white disabled:opacity-40"
          >
            {busy ? <Spinner className="h-4 w-4" /> : <CheckIcon className="h-4 w-4" />}
            Approve
          </button>
          <button
            disabled={busy}
            onClick={() => decide("deny")}
            className="flex h-10 flex-1 items-center justify-center gap-1.5 rounded-lg border border-red-950 bg-red-950/30 text-sm font-semibold text-del active:bg-red-950/60 disabled:opacity-40"
          >
            <XIcon className="h-4 w-4" />
            Deny
          </button>
        </div>
      )}
      {error ? <p className="mt-2 text-xs text-del">{error}</p> : null}
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
      <div className="max-w-full">
        <Markdown text={resultSummary} />
      </div>
    ) : null;
  }
  return (
    <div className="rounded-xl border border-line bg-surface px-3.5 py-3">
      <div className="flex items-center gap-2">
        <CheckIcon className="h-4 w-4 text-add" />
        <span className="text-xs font-semibold uppercase tracking-wide text-muted">
          Task completed
        </span>
      </div>
      {resultSummary ? (
        <p className="mt-1.5 whitespace-pre-wrap text-sm text-fg">{resultSummary}</p>
      ) : null}
      <div className="mt-2 flex flex-wrap gap-3 font-mono text-[11px] text-faint">
        {branch ? <span>branch {branch}</span> : null}
        <span>{iterations} iterations</span>
        <span>{tokensUsed.toLocaleString()} tokens</span>
      </div>
      {prUrl ? (
        <a
          href={prUrl}
          target="_blank"
          rel="noreferrer"
          className="mt-3 flex h-10 items-center justify-center gap-2 rounded-lg bg-accent text-sm font-semibold text-canvas active:bg-white"
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
          <span className="font-mono text-[10px] text-faint">
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
        <div className="rounded-xl border border-line bg-surface px-3.5 py-3">
          <div className="flex items-center gap-2">
            <GlobeIcon className="h-4 w-4 text-muted" />
            <span className="text-xs font-semibold uppercase tracking-wide text-muted">
              Site deployed
            </span>
          </div>
          {title ? <p className="mt-1 text-sm text-fg">{title}</p> : null}
          <div className="mt-2.5 flex flex-col gap-2">
            {persistent ? (
              <a
                href={persistent}
                target="_blank"
                rel="noreferrer"
                className="flex h-10 items-center justify-center gap-2 rounded-lg bg-accent text-sm font-semibold text-canvas active:bg-white"
              >
                <GlobeIcon className="h-4 w-4" /> Open site
              </a>
            ) : live ? (
              <a
                href={live}
                target="_blank"
                rel="noreferrer"
                className="flex h-10 items-center justify-center gap-2 rounded-lg bg-accent text-sm font-semibold text-canvas active:bg-white"
              >
                <GlobeIcon className="h-4 w-4" /> Open live preview
              </a>
            ) : backup ? (
              <a
                href={backup}
                target="_blank"
                rel="noreferrer"
                className="flex h-10 items-center justify-center gap-2 rounded-lg bg-accent text-sm font-semibold text-canvas active:bg-white"
              >
                <GlobeIcon className="h-4 w-4" /> Open page
              </a>
            ) : null}
            {persistent && live ? (
              <a
                href={live}
                target="_blank"
                rel="noreferrer"
                className="text-center text-[11px] text-faint"
              >
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
          <span className="rounded-full border border-line bg-elevated px-2.5 py-0.5 text-[11px] text-muted">
            steered: {str(p.message)}
          </span>
        </div>
      );
    case "context_compacted":
      return (
        <div className="flex justify-center py-0.5">
          <span className="font-mono text-[10px] text-faint">context compacted</span>
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
          <span className="text-[11px] text-faint">approval {str(p.decision, "decided")}</span>
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
        <div className="rounded-xl border border-red-950 bg-red-950/25 px-3.5 py-3">
          <div className="flex items-center gap-2">
            <XIcon className="h-4 w-4 text-del" />
            <span className="text-xs font-semibold uppercase tracking-wide text-del">
              Task failed
            </span>
          </div>
          <p className="mt-1.5 whitespace-pre-wrap text-sm text-muted">{str(p.error)}</p>
        </div>
      );
    case "task_stopped":
      return (
        <div className="rounded-xl border border-line bg-surface px-3.5 py-2.5">
          <p className="text-xs text-muted">Stopped — {str(p.reason, "no reason given")}</p>
        </div>
      );
    case "checkpoint":
      return (
        <div className="flex items-center gap-3 py-1">
          <span className="h-px flex-1 bg-line" />
          <span className="font-mono text-[10px] uppercase tracking-wider text-faint">
            iteration {num(p.iteration)}
          </span>
          <span className="h-px flex-1 bg-line" />
        </div>
      );
    case "error":
      return (
        <div className="rounded-xl border border-red-950 bg-red-950/25 px-3 py-2">
          <p className="text-xs text-del">{str(p.message, "unknown error")}</p>
        </div>
      );
    case "task_started":
      return (
        <div className="flex items-center gap-3 py-1">
          <span className="h-px flex-1 bg-line" />
          <span className="font-mono text-[10px] uppercase tracking-wider text-faint">
            agent started · {str(p.model, "model")}
          </span>
          <span className="h-px flex-1 bg-line" />
        </div>
      );
    default:
      return (
        <div className="rounded-xl border border-line bg-surface px-3 py-2">
          <p className="font-mono text-[11px] text-faint">{event.type}</p>
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
    <div className="overflow-hidden rounded-xl border border-line bg-surface">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-[11px] text-faint active:text-muted"
      >
        {!expanded ? <span className="pulse-dot h-1.5 w-1.5 shrink-0 rounded-full bg-muted" /> : null}
        <span className="shrink-0 font-medium text-muted">
          {events.length} step{events.length === 1 ? "" : "s"}
        </span>
        {!expanded && tail ? (
          <span className="min-w-0 flex-1 truncate font-mono">{workLabel(tail)}</span>
        ) : (
          <span className="flex-1" />
        )}
        <ChevronIcon
          className={`h-3.5 w-3.5 shrink-0 transition-transform ${expanded ? "rotate-90" : ""}`}
        />
      </button>
      {expanded ? (
        <div className="flex flex-col gap-1.5 border-t border-line px-2.5 py-2">
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
        <div className="border-t border-line px-2.5 py-1.5">
          <EventCard event={tail} compact decidedApprovals={decidedApprovals} onDecided={onDecided} />
        </div>
      ) : null}
    </div>
  );
}
