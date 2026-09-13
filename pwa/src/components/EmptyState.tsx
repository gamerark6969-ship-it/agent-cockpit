import type { ReactNode } from "react";

export default function EmptyState({
  title,
  description,
  action,
  icon,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="animate-fade-in flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-line bg-surface/40 px-6 py-14 text-center">
      {icon ? (
        <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-line bg-surface text-faint">
          {icon}
        </div>
      ) : null}
      <p className="text-sm font-medium text-muted">{title}</p>
      {description ? (
        <p className="max-w-xs text-[13px] leading-relaxed text-faint">{description}</p>
      ) : null}
      {action}
    </div>
  );
}
