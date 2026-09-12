import type { TaskStatus } from "../lib/api";
import { STATUS_CLASSES, STATUS_LABELS } from "../lib/utils";

const DOTS: Partial<Record<TaskStatus, string>> = {
  queued: "bg-zinc-400",
  running: "bg-blue-400",
  awaiting_approval: "bg-amber-400",
  done: "bg-emerald-400",
  failed: "bg-red-400",
  stopped: "bg-zinc-500",
};

export default function StatusBadge({
  status,
  compact = false,
}: {
  status: TaskStatus;
  compact?: boolean;
}) {
  const classes = STATUS_CLASSES[status] || STATUS_CLASSES.queued;
  const live = status === "running" || status === "awaiting_approval";
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${classes}`}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${DOTS[status] || DOTS.queued} ${
          live ? "pulse-dot" : ""
        }`}
      />
      {compact ? STATUS_LABELS[status].split(" ")[0] : STATUS_LABELS[status]}
    </span>
  );
}
