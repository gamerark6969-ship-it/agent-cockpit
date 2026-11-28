import { useEffect, useState } from "react";
import {
  apiFetch,
  createConnector,
  deleteConnector,
  listConnectors,
  updateConnector,
  type Connector,
} from "../lib/api";
import Spinner from "../components/Spinner";
import { TrashIcon } from "../components/Icons";
import { btnGhost, btnPrimary, card, inputBase } from "../lib/ui";

interface Field {
  key: string;
  label: string;
  placeholder?: string;
  type?: string;
}

interface CatalogItem {
  kind: string;
  title: string;
  blurb: string;
  fields: Field[];
}

const CATALOG: CatalogItem[] = [
  ...[
    ["freegg", "FreeGG · GPT-6.1 Sol", "https://freegg.app/v1"],
    ["agentrouter-openai", "AgentRouter · GPT-6.1 Sol", "https://agentrouter.org/v1"],
    ["agentrouter-openai-two", "AgentRouter Two · GPT-6.1 Sol", "https://agentrouter.org/v1"],
    ["xingchi", "Xingchi · GPT-6.1 Sol", "https://api.xingchiapi.com/v1"],
    ["xingchi2", "Xingchi 2 · GPT-6.1 Sol", "https://api.xingchiapi.com/v1"],
  ].map(([kind,title,url]) => ({
    kind, title, blurb: url,
    fields: [
      { key: "api_key", label: "API key", placeholder: "API key (leave blank to keep the saved key)", type: "password" },
      { key: "base_url", label: "API base URL", placeholder: url },
    ],
  })),
  {
    kind: "github",
    title: "GitHub",
    blurb: "Read repos and issues, open issues. Auto-configured from your token.",
    fields: [{ key: "pat", label: "Personal access token", placeholder: "github_pat_…", type: "password" }],
  },
  {
    kind: "gmail",
    title: "Gmail",
    blurb: "Read and send email via IMAP + SMTP using a Google App Password.",
    fields: [
      { key: "email", label: "Gmail address", placeholder: "you@gmail.com" },
      { key: "password", label: "App password", placeholder: "16-character app password", type: "password" },
    ],
  },
  {
    kind: "slack",
    title: "Slack",
    blurb: "Post messages with a bot token or an incoming webhook.",
    fields: [
      { key: "bot_token", label: "Bot token (optional)", placeholder: "xoxb-…", type: "password" },
      { key: "webhook_url", label: "Webhook URL (optional)", placeholder: "https://hooks.slack.com/…" },
    ],
  },
  {
    kind: "notion",
    title: "Notion",
    blurb: "Search your workspace pages and databases.",
    fields: [{ key: "token", label: "Integration token", placeholder: "secret_…", type: "password" }],
  },
  {
    kind: "google",
    title: "Google Calendar & Drive",
    blurb: "Coming soon — requires OAuth. Gmail works today via app password.",
    fields: [],
  },
];

export default function Connectors() {
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<"models" | "tools">("models");
  const [checks, setChecks] = useState<Record<string, { ok: boolean; detail: string }>>({});
  const [checking, setChecking] = useState("");

  const load = async () => {
    try {
      setError("");
      setChecks({});
      setConnectors(await listConnectors());
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to load connectors");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const byKind = (kind: string) => connectors.find((c) => c.kind === kind);
  const isModel = (item: CatalogItem) => item.fields.some(field => field.key === "api_key");

  const check = async (connector: Connector) => {
    setChecking(connector.id);
    try {
      const result = await apiFetch<{ ok: boolean; detail: string }>(`/api/connectors/${connector.id}/check-model`, { method: "POST" });
      setChecks(last => ({ ...last, [connector.id]: result }));
    } catch (err) {
      setChecks(last => ({ ...last, [connector.id]: { ok: false, detail: err instanceof Error ? err.message : "Catalog check failed." } }));
    } finally {
      setChecking("");
    }
  };

  const save = async (item: CatalogItem) => {
    setBusy(true);
    setError("");
    try {
      const config: Record<string, string> = {};
      for (const f of item.fields) {
        const v = (values[f.key] || "").trim();
        if (v) config[f.key] = v;
      }
      const existing = byKind(item.kind);
      if (existing) {
        await updateConnector(existing.id, { config, enabled: true });
      } else {
        await createConnector({ kind: item.kind, config, enabled: true });
      }
      setEditing(null);
      setValues({});
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to save connector");
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (c: Connector) => {
    setBusy(true);
    try {
      await updateConnector(c.id, { enabled: !c.enabled });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to update connector");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (c: Connector) => {
    if (!window.confirm(`Disconnect ${c.name}?`)) return;
    setBusy(true);
    try {
      await deleteConnector(c.id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to delete connector");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="safe-top mx-auto max-w-2xl px-4 pb-nav pt-6">
      <header className="mb-5 space-y-3">
        <h1 className="text-3xl font-bold tracking-tight text-fg">Connectors</h1>
        <div className="flex gap-2" aria-label="Connector groups">
          {(["models", "tools"] as const).map(group => <button key={group} aria-pressed={tab === group}
            className={tab === group ? btnPrimary : btnGhost} onClick={() => { setTab(group); setEditing(null); setValues({}); }}>
            {group === "models" ? "Model providers" : "Other tools"}
          </button>)}
        </div>
      </header>

      {error ? (
        <p className="animate-fade-in mb-3 rounded-xl border border-red-950 bg-red-950/25 px-3.5 py-2.5 text-xs text-del">
          {error}
        </p>
      ) : null}

      {loading ? (
        <div className="flex justify-center py-16 text-faint">
          <Spinner />
        </div>
      ) : (
        <div className="flex flex-col gap-2.5">
          {CATALOG.filter(item => isModel(item) === (tab === "models")).map((item, i) => {
            const c = byKind(item.kind);
            const isEditing = editing === item.kind;
            const hasFields = item.fields.length > 0;
            return (
              <div
                key={item.kind}
                className={`${card} animate-rise p-4`}
                style={{ animationDelay: `${Math.min(i, 8) * 30}ms` }}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="font-semibold text-fg">{item.title}</h2>
                    <p className="mt-1 break-words text-xs text-muted">{isModel(item) ? c?.config.base_url || item.blurb : item.blurb}</p>
                  </div>
                  {c ? (
                    <button
                      onClick={() => void toggle(c)}
                      disabled={busy || !!checking}
                      aria-label={`${c.enabled ? "Disable" : "Enable"} ${item.title}`}
                      aria-pressed={c.enabled}
                      className={`min-h-11 shrink-0 rounded-xl px-3 text-xs font-semibold ${
                        c.enabled
                          ? "bg-accent text-canvas"
                          : "border border-line text-faint"
                      }`}
                    >
                      {c.enabled ? "Enabled" : "Disabled"}
                    </button>
                  ) : null}
                </div>

                {isEditing && hasFields ? (
                  <div className="mt-3 flex flex-col gap-2">
                    {item.fields.map((f) => (
                      <label key={f.key} className="block text-xs font-semibold text-muted">{f.label}
                      <input
                        className={`${inputBase} mt-1`}
                        type={f.type || "text"}
                        aria-label={f.label}
                        placeholder={f.placeholder || f.label}
                        value={values[f.key] || ""}
                        onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
                      /></label>
                    ))}
                    <div className="flex gap-2">
                      <button className={btnPrimary} disabled={busy} onClick={() => void save(item)}>
                        {busy ? <Spinner className="h-4 w-4" /> : null}
                        Save
                      </button>
                      <button className={btnGhost} onClick={() => setEditing(null)}>
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {hasFields ? (
                      <button
                        className={btnGhost}
                        onClick={() => {
                          setEditing(item.kind);
                          setValues(isModel(item) && c ? { base_url: c.config.base_url || "" } : {});
                        }}
                      >
                        {c ? "Update" : "Connect"}
                      </button>
                    ) : (
                      <span className="text-[11px] text-faint">Not available yet</span>
                    )}
                    {isModel(item) && c?.enabled && <button className={btnGhost} disabled={!!checking || busy} onClick={() => void check(c)}>
                      {checking === c.id ? "Checking…" : "Check model access"}
                    </button>}
                    {c ? (
                      <button
                        className="flex h-11 w-11 items-center justify-center rounded-xl border border-line text-faint active:text-del"
                        onClick={() => void remove(c)}
                        disabled={busy}
                        aria-label="Disconnect"
                      >
                        <TrashIcon className="h-4 w-4" />
                      </button>
                    ) : null}
                  </div>
                )}
                {c && checks[c.id] && <p role="status" className={`mt-3 text-sm ${checks[c.id].ok ? "text-muted" : "text-del"}`}>{checks[c.id].detail}</p>}
              </div>
            );
          })}
        </div>
      )}

    </main>
  );
}
