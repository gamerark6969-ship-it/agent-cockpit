import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { listApprovals } from "../lib/api";
import { ChatIcon, ListIcon, VideoIcon, PlugIcon, SettingsIcon, RepoIcon, WarningIcon } from "./Icons";

const primary = [
  { to: "/studio", name: "Studio", Icon: VideoIcon },
  { to: "/chats", name: "Chats", Icon: ChatIcon },
  { to: "/reminders", name: "Reminders", Icon: ListIcon },
];
const secondary = [
  { to: "/connectors", name: "Connectors", Icon: PlugIcon },
  { to: "/approvals", name: "Approvals", Icon: WarningIcon },
  { to: "/projects", name: "Projects", Icon: RepoIcon },
  { to: "/settings", name: "Settings", Icon: SettingsIcon },
];

export default function BottomNav() {
  const location = useLocation();
  const [pending, setPending] = useState(0);
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const sheet = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    let alive = true;
    const load = () => void listApprovals(true).then(rows => { if (alive) setPending(rows.length); }).catch(() => {});
    load();
    const id = window.setInterval(load, 20000);
    return () => { alive = false; window.clearInterval(id); };
  }, []);
  useEffect(() => { setOpen(false); }, [location.pathname]);
  useEffect(() => {
    if (open) sheet.current?.showModal();
    else if (sheet.current?.open) sheet.current.close();
  }, [open]);
  return <>
    <nav aria-label="Main navigation" className="app-nav safe-bottom"><div>
      {primary.map(({to,name,Icon}) => <NavLink key={to} to={to} className={location.pathname.startsWith("/c/") && to === "/chats" ? "current" : undefined}>
        <Icon className="h-5 w-5"/>{name}</NavLink>)}
      <button ref={trigger} aria-haspopup="dialog" aria-expanded={open} className={secondary.some(item => location.pathname.startsWith(item.to)) ? "current" : undefined} onClick={() => setOpen(true)}>
        <span className="nav-dots" aria-hidden="true">•••</span>More{pending > 0 && <span className="sr-only">, {pending} pending approvals</span>}
      </button>
    </div></nav>
    <dialog aria-labelledby="more-heading" className="nav-sheet" ref={sheet} onClose={() => { setOpen(false); trigger.current?.focus(); }} onClick={e => { if (e.target === sheet.current) setOpen(false); }}>
      <div><div className="mb-3 flex items-center justify-between"><h2 id="more-heading" className="text-xl font-semibold">More</h2><button type="button" onClick={() => setOpen(false)}>Close</button></div>
      {secondary.map(({to,name,Icon}) => <NavLink key={to} to={to} onClick={() => setOpen(false)}><Icon className="h-5 w-5"/>{name}{name === "Approvals" && pending > 0 && <span>{pending}</span>}</NavLink>)}</div>
    </dialog>
  </>;
}
