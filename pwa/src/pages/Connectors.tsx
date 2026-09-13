import { useEffect, useState } from "react";
import {
  createConnector,
  deleteConnector,
  listConnectors,
  updateConnector,
  type Connector,
} from "../lib/api";
import Spinner from "../components/Spinner";
import { CheckIcon, PlugIcon, TrashIcon } from "../components/Icons";
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

  const load = async () => {
    try {
      setError("");
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
    <div className="safe-top px-4 pb-nav pt-5">
      <header className="mb-5">
        <div className="flex items-center gap-2">
          <PlugIcon className="h-5 w-5 text-muted" />
          <h1 className="text-xl font-semibold tracking-tight text-fg">Connectors</h1>
        </div>
        <p className="mt-0.5 text-[13px] text-faint">Give the agent access to your tools</p>
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
          {CATALOG.map((item, i) => {
            const c = byKind(item.kind);
            const isEditing = editing === item.kind;
            const hasFields = item.fields.length > 0;
            return (
              <div
                key={item.kind}
                className={`${card} animate-rise`}
                style={{ animationDelay: `${Math.min(i, 8) * 30}ms` }}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-semibold text-fg">{item.title}</p>
                      {c?.enabled ? (
                        <span className="flex items-center gap-1 rounded-full border border-line bg-elevated px-2 py-0.5 text-[10px] font-medium text-muted">
                          <CheckIcon className="h-3 w-3" /> connected
                        </span>
                      ) : null}
                    </div>
                    <p className="mt-0.5 text-xs text-faint">{item.blurb}</p>
                    {c && Object.keys(c.config).length ? (
                      <p className="mt-1 truncate font-mono text-[10px] text-faint">
                        {Object.entries(c.config)
                          .map(([k, v]) => `${k}: ${v}`)
                          .join("  ·  ")}
                      </p>
                    ) : null}
                  </div>
                  {c ? (
                    <button
                      onClick={() => void toggle(c)}
                      disabled={busy}
                      className={`shrink-0 rounded-full px-3 py-1 text-[11px] font-semibold ${
                        c.enabled
                          ? "bg-accent text-canvas"
                          : "border border-line text-faint"
                      }`}
                    >
                      {c.enabled ? "on" : "off"}
                    </button>
                  ) : null}
                </div>

                {isEditing && hasFields ? (
                  <div className="mt-3 flex flex-col gap-2">
                    {item.fields.map((f) => (
                      <input
                        key={f.key}
                        className={inputBase}
                        type={f.type || "text"}
                        placeholder={f.placeholder || f.label}
                        value={values[f.key] || ""}
                        onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
                      />
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
                  <div className="mt-3 flex gap-2">
                    {hasFields ? (
                      <button
                        className={btnGhost}
                        onClick={() => {
                          setEditing(item.kind);
                          setValues({});
                        }}
                      >
                        {c ? "Update" : "Connect"}
                      </button>
                    ) : (
                      <span className="text-[11px] text-faint">Not available yet</span>
                    )}
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
              </div>
            );
          })}
        </div>
      )}

      <p className="mt-4 text-center text-[11px] text-faint">
        Secrets are encrypted at rest and never shown again after saving.
      </p>
    </div>
  );
}
