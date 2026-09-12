import type { ReactNode } from "react";

export default function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-zinc-800 px-6 py-12 text-center">
      <p className="text-sm font-medium text-zinc-300">{title}</p>
      {description ? <p className="max-w-xs text-xs text-zinc-500">{description}</p> : null}
      {action}
    </div>
  );
}
