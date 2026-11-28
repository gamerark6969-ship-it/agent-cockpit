import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import ChartMusic from "../components/ChartMusic";
import * as api from "../lib/api";
import { btnGhost, btnPrimary, card, inputBase, label } from "../lib/ui";

const blank = (): api.AutomationSeriesInput => ({
  name: "", brief: "", model: null,
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  publish_times: ["09:00"], enabled: false, publish_mode: "review", privacy: "public", language: "en",
  voice: "en-US-AriaNeural", made_for_kids: false, contains_synthetic_media: false,
});
const terminal = new Set(["published", "uploaded", "failed", "cancelled"]);
const modelLabel = (name: string) => {
  if (name === "auto/gpt-6.1-sol") return "GPT-6.1 Sol · FreeGG / AgentRouter / Xingchi failover";
  const labels: Record<string,string> = { freegg:"FreeGG", "agentrouter-openai":"AgentRouter", "agentrouter-openai-two":"AgentRouter Two", xingchi:"Xingchi", xingchi2:"Xingchi 2" };
  const provider = name.split("/")[0];
  return labels[provider] ? `GPT-6.1 Sol · ${labels[provider]}` : name.replace("opencode/", "OpenCode · ").replace("local/", "Free CPU · ");
};

export default function Automation() {
  const [search] = useSearchParams();
  const [status, setStatus] = useState<api.AutomationStatus | null>(null);
  const [series, setSeries] = useState<api.AutomationSeries[]>([]);
  const [runs, setRuns] = useState<api.AutomationRun[]>([]);
  const [selected, setSelected] = useState(search.get("run") || "");
  const [detail, setDetail] = useState<api.AutomationRun | null>(null);
  const [form, setForm] = useState(blank);
  const [editing, setEditing] = useState("");
  const [music, setMusic] = useState<File | null>(null);
  const [removeMusic, setRemoveMusic] = useState(false);
  const currentMusic = series.find(item => item.id === editing)?.music_name;
  const [showForm, setShowForm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const mounted = useRef(true);
  const refreshSequence = useRef(0);
  const detailSequence = useRef(0);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const mediaCache = useRef({ path: "", url: "", expires: 0 });
  const reload = useCallback(async () => {
    const request = ++refreshSequence.current;
    const [health, profiles, recent] = await Promise.all([
      api.getAutomationStatus(), api.listAutomationSeries(), api.listAutomationRuns(),
    ]);
    if (mounted.current && request === refreshSequence.current) {
      setStatus(health); setSeries(profiles); setRuns(recent);
    }
  }, []);

  useEffect(() => {
    let alive = true;
    mounted.current = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { if (alive && !document.hidden) await reload(); }
      catch (err) { if (alive) setError(err instanceof Error ? err.message : "Studio could not connect"); }
      finally { if (alive) timer = setTimeout(() => void poll(), 15000); }
    };
    void poll();
    return () => { alive = false; mounted.current = false; ++refreshSequence.current; clearTimeout(timer); };
  }, [reload]);

  useEffect(() => {
    if (!selected) { setDetail(null); return; }
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      const request = ++detailSequence.current;
      try {
        if (document.hidden) return;
        const run = await api.getAutomationRun(selected);
        if (alive && request === detailSequence.current) setDetail(run);
      } catch (err) { if (alive) setError(err instanceof Error ? err.message : "Run could not load"); }
      finally { if (alive) timer = setTimeout(() => void poll(), 8000); }
    };
    setDetail(null); void poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [selected]);

  async function perform(action: () => Promise<unknown>, success = "Saved") {
    setBusy(true); setError(""); setMessage("");
    try { await action(); setMessage(success); await reload(); }
    catch (err) { setError(err instanceof Error ? err.message : "The action failed"); }
    finally { setBusy(false); }
  }
  async function runAction(action: (id: string) => Promise<api.AutomationRun>) {
    if (!detail) return;
    const id = detail.id;
    ++detailSequence.current;
    await perform(async () => {
      const result = await action(id);
      ++detailSequence.current;
      if (selectedRef.current === id) setDetail(result);
    }, "Run updated");
  }
  function edit(item: api.AutomationSeries) {
    const { id, created_at: _created, updated_at: _updated, music_name: _music, ...input } = item;
    setMusic(null); setRemoveMusic(false);
    setEditing(id); setForm({ ...input, publish_times: [...input.publish_times] }); setShowForm(true);
  }
  const update = (patch: Partial<api.AutomationSeriesInput>) => setForm(current => ({ ...current, ...patch }));
  let playbackUrl = "";
  if (detail?.media_url) {
    const url = new URL(detail.media_url, api.API_BASE || window.location.origin);
    const path = `${detail.id}:${url.origin}${url.pathname}`;
    if (path !== mediaCache.current.path || mediaCache.current.expires < Date.now() + 30000) {
      let expires = Number(url.searchParams.get("expires")) * 1000;
      if (!expires) {
        const date = url.searchParams.get("X-Amz-Date") || "";
        const parsed = Date.parse(date.replace(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/, "$1-$2-$3T$4:$5:$6Z"));
        expires = parsed + Number(url.searchParams.get("X-Amz-Expires")) * 1000;
      }
      mediaCache.current = { path, url: url.href, expires: expires || Date.now() + 300000 };
    }
    playbackUrl = mediaCache.current.url;
  }

  return <main className="mx-auto max-w-6xl px-4 py-7 sm:px-7">
    <header className="mb-7 flex items-start justify-between gap-3">
      <div><p className="mb-2 text-xs font-medium uppercase tracking-widest text-emerald-400">YouTube automation</p>
        <h1 className="text-3xl font-semibold tracking-tight">Your video studio</h1>
        <p className="mt-2 text-sm text-muted">Original stories. Daily production. Everything from your phone.</p></div>
      <button className={btnPrimary} disabled={busy} onClick={() => { setEditing(""); setMusic(null); setRemoveMusic(false); setForm(blank()); setShowForm(true); }}>New series</button>
    </header>
    {error && <p role="alert" className="mb-4 rounded-xl border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">{error}</p>}
    {message && <p role="status" className="mb-4 rounded-xl bg-emerald-500/10 p-3 text-sm text-emerald-300">{message}</p>}
    <section className="mb-6 grid gap-3 sm:grid-cols-3" aria-label="Connection status">
      <div className={`${card} p-4`}><p className="text-xs text-muted">YouTube channel</p>
        <p className="my-2 font-medium">{status?.youtube.connected ? status.youtube.channel_title || "Connected" : "Connect your channel"}</p>
        <button className={btnGhost} disabled={busy || !status?.youtube.configured} onClick={() => void perform(async () => {
          if (status?.youtube.connected) { await api.disconnectYouTube(); return; }
          const result = await api.getYouTubeAuthorizationUrl(); window.location.assign(result.authorization_url);
        }, "Channel connection updated")}>{status?.youtube.connected ? "Disconnect" : "Connect with Google"}</button>
        {!status?.youtube.configured && <p className="mt-2 text-xs text-muted">Set the YouTube OAuth settings on the backend.</p>}</div>
      <div className={`${card} p-4`}><p className="text-xs text-muted">Production engine</p>
        <p className="my-2 font-medium">{status?.renderer.available ? "Renderer ready" : "Renderer needs setup"}</p>
        <p className="text-xs leading-relaxed text-muted">{status?.renderer.detail || "Checking the backend…"}</p>
        <p className="mt-2 text-xs text-muted">{!status ? "Checking worker…" : status.worker.running ? "Local worker is online" : status.worker.enabled ? "Local worker is offline" : "Local worker is disabled"}</p></div>
      <div className={`${card} p-4`}><p className="text-xs text-muted">Generation & storage</p>
        <p className="my-2 font-medium">{status?.models.length || 0} available models</p>
        <p className="text-xs text-muted">FreeGG and OpenCode, with a free CPU backup for quota outages. Configured GPT routes are selectable.</p>
        <p className="mt-2 text-xs text-muted">{status?.storage.persistent ? "Persistent media storage" : "Local media: connect persistent storage for cloud use"}</p></div>
    </section>
    {showForm && <form className={`${card} mb-6 space-y-4 p-5`} onSubmit={event => {
      event.preventDefault();
      void perform(async () => {
        if (editing) {
          if (music) await api.setSeriesMusic(editing, music);
          else if (removeMusic) await api.clearSeriesMusic(editing);
          await api.updateAutomationSeries(editing, form);
        } else {
          const created = await api.createAutomationSeries(music ? { ...form, enabled: false } : form);
          setEditing(created.id);
          setSeries(items => [...items.filter(item => item.id !== created.id), created]);
          if (music) {
            await api.setSeriesMusic(created.id, music);
            if (form.enabled) await api.updateAutomationSeries(created.id, { enabled: true });
          }
        }
        setShowForm(false);
      }, editing ? "Series updated" : "Series created");
    }}>
      <div className="flex items-center justify-between"><h2 className="text-lg font-semibold">{editing ? "Edit series" : "Create a series"}</h2>
        <button type="button" disabled={busy} className={btnGhost} onClick={() => setShowForm(false)}>Close</button></div>
      <label className={label}>Series name<input className={`${inputBase} mt-1`} required maxLength={120} value={form.name} onChange={e => update({ name: e.target.value })} /></label>
      <label className={label}>Creative brief<textarea className={`${inputBase} mt-1 min-h-28`} required minLength={10} maxLength={8000} value={form.brief}
        placeholder="Describe your audience, characters, tone, and the stories you want to tell."
        onChange={e => update({ brief: e.target.value })} /></label>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className={label}>Model<select className={`${inputBase} mt-1`} value={form.model || ""} onChange={e => update({ model: e.target.value || null })}>
          <option value="">Automatic · FreeGG / OpenCode / CPU</option>
          {form.model && !status?.models.includes(form.model) && <option value={form.model}>{modelLabel(form.model)} · unavailable</option>}
          {status?.models.map(model => <option key={model} value={model}>{modelLabel(model)}</option>)}
        </select></label>
        <label className={label}>Timezone<input className={`${inputBase} mt-1`} required value={form.timezone} onChange={e => update({ timezone: e.target.value })} /></label>
        <label className={label}>Publishing mode<select className={`${inputBase} mt-1`} value={form.publish_mode} onChange={e => update({ publish_mode: e.target.value as "review" | "auto" })}>
          <option value="review">Review on phone before publishing</option><option value="auto">Publish automatically</option></select></label>
        <label className={label}>Language<input className={`${inputBase} mt-1`} required value={form.language} onChange={e => update({ language: e.target.value })} placeholder="en" /></label>
        <label className={label}>Upload visibility<select className={`${inputBase} mt-1`} value={form.privacy} onChange={e => update({ privacy: e.target.value as api.AutomationSeriesInput["privacy"] })}>
          <option value="public">Public</option><option value="unlisted">Unlisted</option><option value="private">Private · commissioning test</option></select></label>
        <label className={label}>Voice<input className={`${inputBase} mt-1`} required value={form.voice} onChange={e => update({ voice: e.target.value })} /></label>
      </div>
      <fieldset disabled={busy} className="min-w-0 space-y-2"><legend className={label}>Background music</legend>
        <p className="text-xs text-muted">Choose an MP3 up to 12 MiB that you own or have licensed for YouTube. New videos in this series reuse the track.</p>
        <a href="https://charts.youtube.com/charts/TopShortsSongs/global/daily" target="_blank" rel="noreferrer" className="inline-block py-1 text-sm text-emerald-400 underline">Explore top songs on YouTube Shorts ↗</a>
        {currentMusic && <p className="break-all text-sm">{removeMusic ? "Track will be removed on save" : `Current track · ${currentMusic}`}</p>}
        <input key={editing + String(showForm)} aria-label="Select background MP3" type="file" accept=".mp3,audio/mpeg" className={`${inputBase} w-full min-w-0 text-sm`}
          onChange={event => {
            const file = event.target.files?.[0] || null;
            if (file && (!file.size || file.size > 12 * 1024 * 1024 || !/\.mp3$/i.test(file.name))) {
              setError("Choose a nonempty MP3 up to 12 MiB."); event.target.value = ""; setMusic(null); return;
            }
            setError(""); setMusic(file); setRemoveMusic(false);
          }} />
        {music && <p className="break-all text-xs text-muted">Selected · {music.name}</p>}
        {currentMusic && !music && <button type="button" className={btnGhost} onClick={() => setRemoveMusic(!removeMusic)}>{removeMusic ? "Keep track" : "Remove track"}</button>}
      </fieldset>
      <fieldset><legend className={label}>Daily production times · 1–3 slots</legend><div className="mt-2 flex flex-wrap items-center gap-2">
        {form.publish_times.map((time,index) => <div className="flex items-center gap-1" key={index}>
          <input aria-label={`Production time ${index+1}`} type="time" required className={inputBase} value={time} onChange={e => update({ publish_times: form.publish_times.map((t,i) => i === index ? e.target.value : t) })} />
          {form.publish_times.length > 1 && <button className={btnGhost} type="button" aria-label={`Remove time ${index+1}`} onClick={() => update({ publish_times: form.publish_times.filter((_,i) => i !== index) })}>×</button>}
        </div>)}
        {form.publish_times.length < 3 && <button className={btnGhost} type="button" onClick={() => update({ publish_times: [...form.publish_times, "17:00"] })}>Add time</button>}
      </div><p className="mt-2 text-xs text-muted">Production starts at these times; free cloud schedules can be delayed.</p></fieldset>
      <div className="space-y-3 text-sm">
        <label className="flex items-center gap-2"><input type="checkbox" checked={form.enabled} onChange={e => update({ enabled: e.target.checked })} />Enable daily production</label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={form.made_for_kids} onChange={e => update({ made_for_kids: e.target.checked })} />This series is made for kids</label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={form.contains_synthetic_media} onChange={e => update({ contains_synthetic_media: e.target.checked })} />Content needs YouTube’s altered/synthetic media disclosure</label>
      </div><button className={btnPrimary} disabled={busy}>{busy ? "Saving…" : "Save series"}</button>
    </form>}
    <div className="grid items-start gap-6 lg:grid-cols-[.9fr_1.1fr]">
      <div className="space-y-6"><section><h2 className="mb-3 text-lg font-semibold">Your series</h2>
        {!series.length && <div className={`${card} p-6 text-sm text-muted`}>Create your first series to set its creative direction and daily schedule.</div>}
        <div className="space-y-3">{series.map(item => <article key={item.id} className={`${card} p-4`}>
          <div className="flex justify-between gap-3"><h3 className="font-medium">{item.name}</h3><span className="text-xs text-emerald-400">{item.enabled ? "Scheduled" : "Paused"}</span></div>
          <p className="mt-2 line-clamp-2 text-sm text-muted">{item.brief}</p>
          {item.music_name && <p className="mt-2 break-all text-xs text-muted">Music · {item.music_name}</p>}
          <p className="mt-3 text-xs text-muted">{item.publish_times.join(" · ")} / {item.timezone} · {item.publish_mode === "auto" ? "Automatic" : "Review first"}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button className={btnPrimary} disabled={busy} onClick={() => void perform(async () => { const run = await api.startAutomationRun(item.id); setSelected(run.id); }, "Run queued for production")}>Run now</button>
            <button className={btnGhost} disabled={busy} onClick={() => edit(item)}>Edit</button>
            <button className={btnGhost} disabled={busy} onClick={() => void perform(() => api.updateAutomationSeries(item.id,{ enabled: !item.enabled }),item.enabled ? "Series paused" : "Schedule enabled")}>{item.enabled ? "Pause" : "Enable"}</button>
          </div><ChartMusic seriesId={item.id} /></article>)}</div></section>
        <section><h2 className="mb-3 text-lg font-semibold">Production history</h2>
          <div className={`${card} overflow-hidden`}>{!runs.length && <p className="p-6 text-sm text-muted">Your video runs will appear here.</p>}
            {runs.map(run => <button key={run.id} onClick={() => setSelected(run.id)} className={`block w-full border-b border-white/5 p-4 text-left last:border-0 ${selected === run.id ? "bg-white/5" : "hover:bg-white/5"}`}>
              <div className="flex items-center justify-between gap-3"><span className="truncate text-sm font-medium">{run.title || series.find(s => s.id === run.series_id)?.name || "New video"}</span>
                <span className={`text-xs ${run.status === "failed" ? "text-red-300" : "text-emerald-400"}`}>{run.status}</span></div>
              <p className="mt-1 text-xs text-muted">{new Date(run.created_at).toLocaleString()} · {run.stage}</p></button>)}
          </div></section></div>
      <section className={`${card} p-5 lg:sticky lg:top-5`} aria-label="Selected video run">
        {!detail ? <div className="py-16 text-center"><p className="text-lg font-medium">Follow your next story</p><p className="mt-2 text-sm text-muted">Select a run to see its draft, activity, and publishing controls.</p></div> : <>
          <div className="flex items-start justify-between gap-3"><h2 className="text-xl font-semibold">{detail.title || "Creating your video"}</h2><span className="text-xs text-emerald-400">{detail.status}</span></div>
          <p className="mt-2 text-xs text-muted">Current stage: {detail.stage}</p>
          {playbackUrl && <video key={detail.id} controls playsInline preload="metadata" className="mx-auto mt-5 max-h-[32rem] w-full rounded-xl bg-black" src={playbackUrl} />}
          {detail.error && <p role="alert" className="mt-4 text-sm text-red-300">{detail.error}</p>}
          {detail.description && <p className="mt-4 whitespace-pre-wrap text-sm leading-relaxed text-muted">{detail.description}</p>}
          <div className="my-5 flex flex-wrap gap-2">
            {detail.status === "review" && <button className={btnPrimary} disabled={busy} onClick={() => void runAction(api.approveAutomationRun)}>Approve & publish</button>}
            {detail.status === "failed" && <button className={btnPrimary} disabled={busy} onClick={() => void runAction(api.retryAutomationRun)}>Retry run</button>}
            {!terminal.has(detail.status) && <button className={btnGhost} disabled={busy} onClick={() => void runAction(api.cancelAutomationRun)}>Cancel run</button>}
            {detail.youtube_url && <a className={btnPrimary} href={detail.youtube_url} target="_blank" rel="noreferrer">Watch on YouTube ↗</a>}
          </div>
          <h3 className="mb-3 text-sm font-medium">Activity</h3><ol className="space-y-4">{detail.events?.map((event,index) => <li key={`${event.created_at}-${index}`} className="border-l border-emerald-400/30 pl-3">
            <p className="text-sm">{event.message}</p><time className="text-xs text-muted">{new Date(event.created_at).toLocaleTimeString()}</time></li>)}</ol>
        </>}
      </section>
    </div>
  </main>;
}
