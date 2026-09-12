import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { listApprovals } from "../lib/api";
import { ListIcon, SettingsIcon, WarningIcon } from "./Icons";

const itemBase = "flex flex-1 flex-col items-center justify-center gap-1 py-2 text-[11px]";
const inactive = "text-zinc-500";
const active = "text-emerald-400";

export default function BottomNav() {
  const [pending, setPending] = useState(0);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const rows = await listApprovals(true);
        if (alive) setPending(rows.length);
      } catch {
        /* ignore */
      }
    };
    void load();
    const id = window.setInterval(load, 20000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  return (
    <nav className="safe-bottom fixed inset-x-0 bottom-0 z-30 border-t border-zinc-800 bg-zinc-950/95 backdrop-blur">
      <div className="mx-auto flex max-w-md">
        <NavLink to="/" end className={({ isActive }) => `${itemBase} ${isActive ? active : inactive}`}>
          <ListIcon className="h-5 w-5" />
          Projects
        </NavLink>
        <NavLink
          to="/approvals"
          className={({ isActive }) => `${itemBase} ${isActive ? active : inactive} relative`}
        >
          <span className="relative">
            <WarningIcon className="h-5 w-5" />
            {pending > 0 ? (
              <span className="absolute -right-2 -top-2 flex h-4 min-w-4 items-center justify-center rounded-full bg-amber-500 px-1 text-[10px] font-bold text-black">
                {pending}
              </span>
            ) : null}
          </span>
          Approvals
        </NavLink>
        <NavLink
          to="/settings"
          className={({ isActive }) => `${itemBase} ${isActive ? active : inactive}`}
        >
          <SettingsIcon className="h-5 w-5" />
          Settings
        </NavLink>
      </div>
    </nav>
  );
}
