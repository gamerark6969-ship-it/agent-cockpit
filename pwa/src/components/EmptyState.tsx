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
    <div className="animate-fade-in flex flex-col items-center justify-center gap-3 rounded-3xl border border-dashed border-zinc-800/80 bg-zinc-900/20 px-6 py-14 text-center">
      {icon ? (
        <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-zinc-900 text-zinc-500 ring-1 ring-zinc-800">
          {icon}
        </div>
      ) : null}
      <p className="text-sm font-semibold text-zinc-300">{title}</p>
      {description ? <p className="max-w-xs text-xs leading-relaxed text-zinc-500">{description}</p> : null}
      {action}
    </div>
  );
}
