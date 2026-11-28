import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { apiFetch, AutomationStatus, disconnectYouTube, getYouTubeAuthorizationUrl } from "../lib/api";
import { btnGhost, btnPrimary } from "../lib/ui";

type Props = { youtube?: AutomationStatus["youtube"]; reload: () => Promise<void> | void };

export default function YouTubeConnection({ youtube, reload }: Props) {
  const [params] = useSearchParams();
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [showSetup, setShowSetup] = useState(false);
  const callback = youtube?.redirect_uri;
  const outcome = params.get("youtube");
  const notice = outcome === "cancelled" ? "Google sign-in was cancelled. You can try again."
    : outcome === "error" ? "Google could not complete the connection. Check the callback and your Google test-user access, then reconnect."
    : outcome === "connected" && youtube?.connected ? "Your channel is connected." : "";

  async function action(work: () => Promise<void>) {
    setBusy(true);
    setMessage("");
    try { await work(); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Channel setup failed. Please retry."); }
    finally { setBusy(false); }
  }
  return <section aria-label="YouTube connection" className="min-w-0 space-y-3">
    <div><h3 className="font-medium">YouTube channel</h3>
      <p className="mt-1 text-sm text-muted">{!youtube ? "Checking connection…" : youtube.connected ? youtube.channel_title || "Channel connected" : youtube.configured ? "Ready for Google sign-in" : "Set up Google sign-in to connect your channel."}</p></div>
    {youtube && <div className="flex flex-wrap gap-2">
      {youtube.configured && <button type="button" disabled={busy} className={btnPrimary} onClick={() => void action(async () => {
        const {authorization_url} = await getYouTubeAuthorizationUrl();
        const next = new URL(authorization_url);
        if (next.origin !== "https://accounts.google.com") throw new Error("Unexpected Google sign-in address.");
        window.location.assign(next.href);
      })}>{youtube.connected ? "Reconnect with Google" : "Connect with Google"}</button>}
      {youtube.connected && <button type="button" disabled={busy} className={btnGhost} onClick={() => void action(async () => {
        await disconnectYouTube(); await reload(); setMessage("Channel disconnected.");
      })}>Disconnect</button>}
      {youtube.configured && youtube.client_source !== "server" && <button type="button" className={btnGhost} onClick={() => setShowSetup(!showSetup)}>{showSetup ? "Hide setup" : "Manage Google client"}</button>}
    </div>}
    {youtube && (!youtube.configured || showSetup) && <div className="space-y-3 text-sm">
      {callback ? <>
        <ol className="list-decimal space-y-2 pl-5 text-muted">
          <li>Enable the <a className="underline" href="https://console.cloud.google.com/apis/library/youtube.googleapis.com" target="_blank" rel="noreferrer">YouTube Data API</a> in your Google Cloud project.</li>
          <li>Configure the consent screen, then create a <a className="underline" href="https://console.cloud.google.com/auth/clients" target="_blank" rel="noreferrer">Web application client</a>. If your app is in testing, add your Google account as a test user.</li>
          <li>Add this authorized redirect URI, save the client, and download its JSON.</li>
        </ol>
        <div className="flex flex-wrap items-center gap-2"><code className="min-w-0 break-all rounded bg-surface p-2 text-xs">{callback}</code>
          <button type="button" className={btnGhost} onClick={() => void action(async () => {
            await navigator.clipboard.writeText(callback); setMessage("Callback copied.");
          })}>Copy callback</button></div>
        {youtube.client_source !== "server" && <label className="block">
          <span className="font-medium">{youtube.client_source === "studio" ? "Replace Google client JSON" : "Upload Google client JSON"}</span>
          {youtube.connected && <span className="mt-1 block text-xs text-muted">Choosing a different client ID disconnects the current channel. Reconnect with Google after saving.</span>}
          <input aria-label="Google Web application client JSON" type="file" accept=".json,application/json" disabled={busy} className="mt-2 block w-full text-xs" onChange={event => {
            const file = event.currentTarget.files?.[0]; event.currentTarget.value = "";
            if (!file) return;
            void action(async () => {
              if (file.size > 65536) throw new Error("Choose a Google client JSON smaller than 64 KiB.");
              await apiFetch("/api/automation/youtube/client", {method:"PUT", headers:{"Content-Type":"application/json"}, body:file});
              await reload(); setShowSetup(false); setMessage("Google client saved. Continue with Google sign-in.");
            });
          }} />
        </label>}
      </> : <p className="text-muted">The server needs its public HTTPS address configured before it can generate the Google callback.</p>}
    </div>}
    {(message || notice || busy) && <p role="status" className="break-words text-sm text-muted">{busy ? "Updating connection…" : message || notice}</p>}
  </section>;
}
