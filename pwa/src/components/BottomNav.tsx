import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { listApprovals } from "../lib/api";
import { ChatIcon, PlugIcon, SettingsIcon, WarningIcon } from "./Icons";

const itemBase =
  "relative flex flex-1 flex-col items-center justify-center gap-1 rounded-lg py-2 text-[11px] font-medium transition-colors";

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

  const cls = (isActive: boolean) =>
    `${itemBase} ${isActive ? "text-fg" : "text-faint hover:text-muted"}`;

  return (
    <nav className="safe-bottom fixed inset-x-0 bottom-0 z-30 border-t border-line bg-canvas/95 backdrop-blur">
      <div className="mx-auto flex max-w-md items-center px-2 pt-1.5">
        <NavLink to="/" end className={({ isActive }) => cls(isActive)}>
          <ChatIcon className="h-5 w-5" />
          Chats
        </NavLink>
        <NavLink to="/connectors" className={({ isActive }) => cls(isActive)}>
          <PlugIcon className="h-5 w-5" />
          Connectors
        </NavLink>
        <NavLink to="/approvals" className={({ isActive }) => cls(isActive)}>
          <span className="relative">
            <WarningIcon className="h-5 w-5" />
            {pending > 0 ? (
              <span className="absolute -right-2 -top-2 flex h-4 min-w-4 items-center justify-center rounded-full bg-warn px-1 text-[10px] font-bold text-canvas">
                {pending}
              </span>
            ) : null}
          </span>
          Approvals
        </NavLink>
        <NavLink to="/settings" className={({ isActive }) => cls(isActive)}>
          <SettingsIcon className="h-5 w-5" />
          Settings
        </NavLink>
      </div>
    </nav>
  );
}
