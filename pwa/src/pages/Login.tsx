import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { clearToken, getToken, setToken } from "../lib/api";
import { btnGhost, btnPrimary, inputBase } from "../lib/ui";

export default function Login() {
  const navigate = useNavigate();
  const existing = getToken();
  const [value, setValue] = useState("");
  const [error, setError] = useState("");

  const connect = () => {
    const token = value.trim();
    if (!token) {
      setError("Paste your APP_TOKEN to continue.");
      return;
    }
    setToken(token);
    navigate("/", { replace: true });
  };

  return (
    <div className="safe-top flex min-h-dvh flex-col justify-center px-6">
      <div className="mb-8">
        <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-emerald-600 text-2xl font-black text-white">
          AC
        </div>
        <h1 className="text-2xl font-bold text-zinc-100">Agent Cockpit</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Control an autonomous software engineer from your phone.
        </p>
      </div>

      {existing ? (
        <div className="mb-6 rounded-xl border border-emerald-900/60 bg-emerald-950/20 px-3 py-2.5">
          <p className="text-xs text-emerald-300">A token is already saved on this device.</p>
        </div>
      ) : null}

      <label className="mb-1.5 block text-xs font-medium uppercase tracking-wide text-zinc-500">
        Access token
      </label>
      <input
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
        onKeyDown={(e) => {
          if (e.key === "Enter") connect();
        }}
      />
      {error ? <p className="mt-2 text-xs text-red-400">{error}</p> : null}

      <div className="mt-4 flex flex-col gap-2">
        <button className={btnPrimary} onClick={connect}>
          Connect
        </button>
        {existing ? (
          <>
            <button className={btnGhost} onClick={() => navigate("/", { replace: true })}>
              Continue with saved token
            </button>
            <button
              className="py-2 text-xs text-zinc-500"
              onClick={() => {
                clearToken();
                setValue("");
              }}
            >
              Clear saved token
            </button>
          </>
        ) : null}
      </div>
    </div>
  );
}
