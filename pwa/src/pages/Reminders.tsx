import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { apiFetch } from "../lib/api";
import { btnGhost, btnPrimary, card, inputBase, label } from "../lib/ui";

type Reminder = { id: string; title: string; due_at: string; done: boolean };

export default function Reminders() {
  const [rows, setRows] = useState<Reminder[]>([]);
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(async () => {
    const reminders = await apiFetch<Reminder[]>("/api/reminders");
    setRows(reminders);
  }, []);

  useEffect(() => {
    async function load() {
      try { await refresh(); }
      catch (e) { setError(String(e)); }
      finally { setLoading(false); }
    }
    void load();
    const timer = window.setInterval(load, 30000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  async function act(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try { await action(); await refresh(); }
    catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }

  function add(event: FormEvent) {
    event.preventDefault();
    void act(async () => {
      await apiFetch("/api/reminders", {
        method: "POST", body: JSON.stringify({ title: title.trim(), due_at: new Date(due).toISOString() }),
      });
      setTitle("");
      setDue("");
    });
  }

  return (
    <main className="space-y-5 px-4 py-6">
      <h1 className="text-2xl font-semibold">Reminders</h1>
      <form className={card + " space-y-4 p-4"} onSubmit={add}>
        <label className="block"><span className={label}>Remind me to</span>
          <input className={inputBase} value={title} required maxLength={240} onChange={e => setTitle(e.target.value)} />
        </label>
        <label className="block"><span className={label}>Date and time · local time</span>
          <input className={inputBase} type="datetime-local" required value={due} onChange={e => setDue(e.target.value)} />
        </label>
        <button className={btnPrimary} disabled={busy || !title.trim() || !due}>Add reminder</button>
      </form>
      <p className="text-sm text-muted">Enable phone notifications in <Link to="/settings" className="underline">Settings</Link>.</p>
      {error && <p role="alert" className="text-sm text-del">{error}</p>}
      {loading ? <p>Loading reminders…</p> : !rows.length && !error && <p className="text-muted">No reminders yet.</p>}
      {rows.map(row => (
        <article key={row.id} className={card + " space-y-2 p-4"}>
          <h2 className="break-words font-semibold">{row.title}</h2>
          <p className="text-sm text-muted">{row.done ? "Completed" : Date.parse(row.due_at) <= Date.now() ? "Due" : "Scheduled"} · {new Date(row.due_at).toLocaleString()}</p>
          <div className="flex gap-2">
            {!row.done && <button disabled={busy} className={btnGhost} onClick={() => void act(() => apiFetch<void>(`/api/reminders/${row.id}/complete`, { method: "POST" }))}>Mark done</button>}
            <button disabled={busy} className={btnGhost} onClick={() => void act(() => apiFetch<void>(`/api/reminders/${row.id}`, { method: "DELETE" }))}>Delete</button>
          </div>
        </article>
      ))}
    </main>
  );
}
