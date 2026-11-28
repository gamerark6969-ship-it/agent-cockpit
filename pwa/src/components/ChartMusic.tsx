import { useState } from "react";
import { apiFetch } from "../lib/api";
import { btnGhost, inputBase, label } from "../lib/ui";

type Chart = { playlist_id: string | null; candidates: {slot: number; name: string; video_id: string}[] };

export default function ChartMusic({ seriesId }: { seriesId: string }) {
  const path = `/api/automation/series/${encodeURIComponent(seriesId)}/chart`;
  const [open, setOpen] = useState(false);
  const [chart, setChart] = useState<Chart | null>(null);
  const [playlist, setPlaylist] = useState("");
  const [slot, setSlot] = useState("1");
  const [video, setVideo] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function request(url: string, init?: RequestInit, success = "") {
    setBusy(true);
    setMessage("");
    try {
      const result = await apiFetch<Chart>(url, init);
      setChart(result);
      setPlaylist(result.playlist_id || "");
      setMessage(success);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Unable to update chart music.");
    } finally {
      setBusy(false);
    }
  }

  return <div className="mt-3 border-t border-white/10 pt-2">
    <button type="button" className={btnGhost} aria-expanded={open} onClick={() => {
      setOpen(!open);
      if (!open && !chart) void request(path);
    }}>Chart music {open ? "▴" : "▾"}</button>
    {open && <div className="mt-2 space-y-3 text-sm">
      {chart && <fieldset disabled={busy} className="min-w-0 space-y-3">
        <p className="text-xs text-muted">Choose the highest-ranked licensed track in the first 50 entries of your YouTube playlist.</p>
        <label className={label}>Chart playlist URL or ID<input className={inputBase} value={playlist} onChange={e => setPlaylist(e.target.value)} /></label>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={btnGhost} disabled={!playlist.trim()} onClick={() => void request(path + "?playlist=" + encodeURIComponent(playlist), {method:"PUT"}, "Chart enabled.")}>Use playlist</button>
          {chart.playlist_id && <button type="button" className={btnGhost} onClick={() => void request(path + "?playlist=", {method:"PUT"}, "Chart disabled.")}>Disable chart</button>}
        </div>
        {chart.candidates.map(track => <div key={track.slot} className="flex items-center justify-between gap-2">
          <span className="min-w-0 break-all text-xs">Slot {track.slot} · {track.name} · {track.video_id}</span>
          <button type="button" className={btnGhost} onClick={() => void request(path + "/" + track.slot, {method:"DELETE"}, "Track removed.")}>Remove</button>
        </div>)}
        <label className={label}>Track slot<select className={inputBase} value={slot} onChange={e => setSlot(e.target.value)}>
          {[1,2,3].map(n => <option key={n} value={n}>{n} · {chart.candidates.find(c => c.slot === n)?.name || "Empty"}</option>)}</select></label>
        <label className={label}>YouTube video ID<input className={inputBase} maxLength={11} placeholder="11-character video ID" value={video} onChange={e => setVideo(e.target.value.trim())} /></label>
        <label className={label}>MP3 you own or have licensed (4 MiB max)<input className="mt-1 block w-full text-xs" type="file" accept=".mp3,audio/mpeg" onChange={e => {
          const selected = e.target.files?.[0] ?? null;
          setFile(selected && selected.size <= 4194304 ? selected : null);
          setMessage(selected && selected.size > 4194304 ? "Choose an MP3 up to 4 MiB." : "");
        }} /></label>
        <button type="button" className={btnGhost} disabled={!file || !/^[\w-]{11}$/.test(video)} onClick={() => {
          if (file) void request(`${path}/${slot}?video_id=${encodeURIComponent(video)}&name=${encodeURIComponent(file.name)}`, {method:"PUT",headers:{"Content-Type":"audio/mpeg"},body:file}, "Track saved.");
        }}>{chart.candidates.some(c => c.slot === Number(slot)) ? "Replace track" : "Add track"}</button>
      </fieldset>}
      <p role="status" className="text-xs text-muted">{busy ? "Working…" : message}</p>
    </div>}
  </div>;
}
