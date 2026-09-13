import type { TaskStatus } from "../lib/api";
import { STATUS_CLASSES, STATUS_LABELS } from "../lib/utils";

const DOTS: Partial<Record<TaskStatus, string>> = {
  queued: "bg-faint",
  running: "bg-blue-400",
  awaiting_approval: "bg-warn",
  done: "bg-add",
  failed: "bg-del",
  stopped: "bg-faint",
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
