import type { TaskStatus } from "../lib/api";
import { STATUS_CLASSES, STATUS_LABELS } from "../lib/utils";

export default function StatusBadge({
  status,
  compact = false,
}: {
  status: TaskStatus;
  compact?: boolean;
}) {
  const classes = STATUS_CLASSES[status] || STATUS_CLASSES.queued;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${classes}`}
    >
      {status === "running" ? (
        <span className="pulse-dot h-1.5 w-1.5 rounded-full bg-blue-400" />
      ) : null}
      {status === "awaiting_approval" ? (
        <span className="pulse-dot h-1.5 w-1.5 rounded-full bg-amber-400" />
      ) : null}
      {compact ? STATUS_LABELS[status].split(" ")[0] : STATUS_LABELS[status]}
    </span>
  );
}
