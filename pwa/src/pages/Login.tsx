import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { API_BASE, clearToken, getToken, setToken } from "../lib/api";
import { btnGhost, btnPrimary, inputBase } from "../lib/ui";

export default function Login() {
  const navigate = useNavigate();
  const returnTo = useLocation().state?.returnTo;
  const destination = typeof returnTo === "string" && /^\/(?!\/)/.test(returnTo) && !returnTo.includes("\\") ? returnTo : "/";
  const [existing, setExisting] = useState(getToken);
  const [value, setValue] = useState("");
  const [error, setError] = useState("");

  const [busy, setBusy] = useState(false);
  const connect = async (candidate: string) => {
    const token = candidate.trim();
    if (!token) {
      setError("Paste your APP_TOKEN to continue.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/api/models`, {
        headers: { Authorization: `Bearer ${token}` }, cache: "no-store", redirect: "error",
      });
      if (response.status === 401 || response.status === 403) {
        setError("That access token was rejected. Check your APP_TOKEN and try again.");
        return;
      }
      if (!response.ok || !Array.isArray((await response.json()).models)) throw new Error();
      setToken(token);
      navigate(destination, { replace: true });
    } catch {
      setError("Could not verify the token. Check your connection to the backend and try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="safe-top safe-bottom flex min-h-dvh flex-col justify-center px-6">
      <div className="login-brand"><span className="brand-mark" aria-hidden="true" /><span>Agent Cockpit</span></div>
      <div className="mb-7"><h1 className="text-4xl font-bold tracking-tight">Sign in</h1>
        <p className="mt-2 text-sm text-muted">Enter your workspace access token to open Studio.</p>
      </div>

      {existing ? (
        <div className="mb-6 rounded-xl border border-line bg-surface px-3.5 py-3">
          <p className="text-xs text-muted">A token is already saved on this device.</p>
        </div>
      ) : null}

      <form onSubmit={event => { event.preventDefault(); if (!busy) void connect(value); }}>
      <label htmlFor="access-token" className="mb-1.5 block text-[11px] font-semibold uppercase tracking-wider text-faint">
        Access token
      </label>
      <input
        id="access-token"
        disabled={busy}
        aria-describedby={error ? "login-error" : undefined}
        className={inputBase}
        type="password"
        inputMode="text"
        autoComplete="off"
        placeholder="APP_TOKEN"
        value={value}
        onChange={(e) => {
          setValue(e.target.value);
          setError("");
        }}
      />
      {error ? <p role="alert" id="login-error" className="mt-2 text-xs text-del">{error}</p> : null}

      <div className="mt-4 flex flex-col gap-2">
        <button type="submit" disabled={busy} className={btnPrimary}>
          {busy ? "Connecting…" : "Connect"}
        </button>
        {existing ? (
          <>
            <button type="button" disabled={busy} className={btnGhost} onClick={() => void connect(existing)}>
              Continue with saved token
            </button>
            <button
              type="button"
              disabled={busy}
              className="py-2 text-xs text-faint transition-colors active:text-muted"
              onClick={() => {
                clearToken();
                setExisting("");
                setValue("");
              }}
            >
              Clear saved token
            </button>
          </>
        ) : null}
      </div>
      </form>
    </div>
  );
}
