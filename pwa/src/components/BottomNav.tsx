import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { listApprovals } from "../lib/api";
import { ChatIcon, PlugIcon, SettingsIcon, WarningIcon } from "./Icons";

const itemBase =
  "relative flex flex-1 flex-col items-center justify-center gap-1 rounded-2xl py-2 text-[11px] font-medium transition-colors";

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
    <nav className="safe-bottom pointer-events-none fixed inset-x-0 bottom-0 z-30">
      <div className="mx-auto max-w-md px-3 pb-2">
        <div className="glass pointer-events-auto flex items-center gap-1 rounded-3xl border border-zinc-800/80 p-1.5 shadow-2xl shadow-black/60">
          <NavLink
            to="/"
            end
            className={({ isActive }) =>
              `${itemBase} ${isActive ? "bg-emerald-500/12 text-emerald-400" : "text-zinc-500"}`
            }
          >
            <ChatIcon className="h-5 w-5" />
            Chats
          </NavLink>
          <NavLink
            to="/connectors"
            className={({ isActive }) =>
              `${itemBase} ${isActive ? "bg-emerald-500/12 text-emerald-400" : "text-zinc-500"}`
            }
          >
            <PlugIcon className="h-5 w-5" />
            Connectors
          </NavLink>
          <NavLink
            to="/approvals"
            className={({ isActive }) =>
              `${itemBase} ${isActive ? "bg-emerald-500/12 text-emerald-400" : "text-zinc-500"}`
            }
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
            className={({ isActive }) =>
              `${itemBase} ${isActive ? "bg-emerald-500/12 text-emerald-400" : "text-zinc-500"}`
            }
          >
            <SettingsIcon className="h-5 w-5" />
            Settings
          </NavLink>
        </div>
      </div>
    </nav>
  );
}
