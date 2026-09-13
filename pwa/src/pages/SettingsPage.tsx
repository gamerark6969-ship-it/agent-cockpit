import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  clearToken,
  getSettings,
  getVapidPublicKey,
  putSettings,
  setToken,
  type AppSettings,
  type BashRule,
} from "../lib/api";
import { enablePush, notificationPermission, pushSupported } from "../lib/push";
import Spinner from "../components/Spinner";
import { PlusIcon, TrashIcon } from "../components/Icons";
import { btnGhost, btnPrimary, card, inputBase, label } from "../lib/ui";

export default function SettingsPage() {
  const navigate = useNavigate();
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [token, setTokenValue] = useState("");
  const [vapidKey, setVapidKey] = useState("");
  const [pushMessage, setPushMessage] = useState("");
  const [enablingPush, setEnablingPush] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const s = await getSettings();
        setSettings(s);
      } catch (err) {
        setError(err instanceof Error ? err.message : "failed to load settings");
      } finally {
        setLoading(false);
      }
      try {
        const v = await getVapidPublicKey();
        setVapidKey(v.public_key);
      } catch {
        setVapidKey("");
      }
    })();
  }, []);

  const update = (patch: Partial<AppSettings>) => {
    setSettings((prev) => (prev ? { ...prev, ...patch } : prev));
    setMessage("");
  };

  const updateRule = (index: number, patch: Partial<BashRule>) => {
    if (!settings) return;
    const rules = settings.permissions.bash_rules.map((rule, i) =>
      i === index ? { ...rule, ...patch } : rule,
    );
    update({ permissions: { ...settings.permissions, bash_rules: rules } });
  };

  const addRule = () => {
    if (!settings) return;
    update({
      permissions: {
        ...settings.permissions,
        bash_rules: [...settings.permissions.bash_rules, { match: "", level: "ask" }],
      },
    });
  };

  const removeRule = (index: number) => {
    if (!settings) return;
    update({
      permissions: {
        ...settings.permissions,
        bash_rules: settings.permissions.bash_rules.filter((_, i) => i !== index),
      },
    });
  };

  const save = async () => {
    if (!settings) return;
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const saved = await putSettings({
        ...settings,
        permissions: {
          ...settings.permissions,
          bash_rules: settings.permissions.bash_rules.filter((r) => r.match.trim()),
        },
      });
      setSettings(saved);
      setMessage("Settings saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to save settings");
    } finally {
      setSaving(false);
    }
  };

  const saveToken = () => {
    const value = token.trim();
    if (!value) return;
    setToken(value);
    setTokenValue("");
    setMessage("Token updated on this device.");
  };

  const doEnablePush = async () => {
    setEnablingPush(true);
    setPushMessage("");
    try {
      const result = await enablePush();
      setPushMessage(result.message);
    } catch (err) {
      setPushMessage(err instanceof Error ? err.message : "failed to enable push");
    } finally {
      setEnablingPush(false);
    }
  };

  const permission = notificationPermission();

  if (loading) {
    return (
      <div className="flex justify-center py-16 text-faint">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="safe-top flex flex-col gap-4 px-4 pb-nav pt-5">
      <header>
        <h1 className="text-xl font-semibold tracking-tight text-fg">Settings</h1>
        <p className="mt-0.5 text-[13px] text-faint">Models, permissions, and notifications</p>
      </header>

      {message ? (
        <p className="animate-fade-in rounded-xl border border-line bg-surface px-3.5 py-2.5 text-xs text-muted">
          {message}
        </p>
      ) : null}
      {error ? (
        <p className="animate-fade-in rounded-xl border border-red-950 bg-red-950/25 px-3.5 py-2.5 text-xs text-del">
          {error}
        </p>
      ) : null}

      <section className={card}>
        <p className={label}>Access token</p>
        <div className="mt-2 flex gap-2">
          <input
            className={inputBase}
            type="password"
            autoComplete="off"
            placeholder="Paste new token"
            value={token}
            onChange={(e) => setTokenValue(e.target.value)}
          />
          <button className={btnGhost} onClick={saveToken}>
            Save
          </button>
        </div>
        <button
          className="mt-3 text-xs text-faint"
          onClick={() => {
            clearToken();
            navigate("/login", { replace: true });
          }}
        >
          Sign out / clear token
        </button>
      </section>

      <section className={card}>
        <p className={label}>Notifications</p>
        <p className="mt-1.5 text-xs text-faint">
          Push support: {pushSupported() ? "available" : "unavailable"} · permission: {permission}
        </p>
        <p className="mt-1 break-all font-mono text-[11px] text-faint">
          VAPID key: {vapidKey || "not configured on server"}
        </p>
        {pushMessage ? <p className="mt-2 text-xs text-muted">{pushMessage}</p> : null}
        <button
          className={`${btnPrimary} mt-3`}
          disabled={enablingPush}
          onClick={() => void doEnablePush()}
        >
          {enablingPush ? <Spinner className="h-4 w-4" /> : null}
          Enable notifications on this device
        </button>
        <p className="mt-2 text-[11px] text-faint">
          On iOS, add this app to your home screen first (Share → Add to Home Screen).
        </p>
      </section>

      {settings ? (
        <>
          <section className={card}>
            <p className={label}>Agent defaults</p>
            <div className="mt-3 flex flex-col gap-3">
              <div>
                <label className="mb-1 block text-[11px] text-faint">Default model</label>
                <input
                  className={inputBase}
                  value={settings.default_model}
                  onChange={(e) => update({ default_model: e.target.value })}
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-[11px] text-faint">Max iterations</label>
                  <input
                    className={inputBase}
                    type="number"
                    value={settings.max_iterations}
                    onChange={(e) => update({ max_iterations: Number(e.target.value) || 0 })}
                  />
                </div>
                <div>
                  <label className="mb-1 block text-[11px] text-faint">Token budget</label>
                  <input
                    className={inputBase}
                    type="number"
                    value={settings.token_budget}
                    onChange={(e) => update({ token_budget: Number(e.target.value) || 0 })}
                  />
                </div>
                <div>
                  <label className="mb-1 block text-[11px] text-faint">Command timeout (s)</label>
                  <input
                    className={inputBase}
                    type="number"
                    value={settings.command_timeout_s}
                    onChange={(e) => update({ command_timeout_s: Number(e.target.value) || 0 })}
                  />
                </div>
                <div>
                  <label className="mb-1 block text-[11px] text-faint">Approval timeout (s)</label>
                  <input
                    className={inputBase}
                    type="number"
                    value={settings.approval_timeout_s}
                    onChange={(e) => update({ approval_timeout_s: Number(e.target.value) || 0 })}
                  />
                </div>
                <div>
                  <label className="mb-1 block text-[11px] text-faint">Compaction threshold</label>
                  <input
                    className={inputBase}
                    type="number"
                    value={settings.compaction_threshold_tokens ?? 24000}
                    onChange={(e) =>
                      update({ compaction_threshold_tokens: Number(e.target.value) || 0 })
                    }
                  />
                </div>
              </div>
            </div>
          </section>

          <section className={card}>
            <div className="flex items-center justify-between">
              <p className={label}>Command permission rules</p>
              <button
                onClick={addRule}
                className="flex h-8 w-8 items-center justify-center rounded-lg border border-line bg-surface text-muted active:bg-hover"
                aria-label="Add rule"
              >
                <PlusIcon className="h-4 w-4" />
              </button>
            </div>
            <p className="mt-1.5 text-[11px] text-faint">
              First matching glob wins. Commands with no match default to auto.
            </p>
            <div className="mt-3 flex flex-col gap-2">
              {settings.permissions.bash_rules.map((rule, index) => (
                <div key={index} className="flex items-center gap-2">
                  <input
                    className={`${inputBase} h-10 font-mono text-xs`}
                    placeholder="glob e.g. git push --force*"
                    value={rule.match}
                    onChange={(e) => updateRule(index, { match: e.target.value })}
                  />
                  <select
                    className="h-10 shrink-0 rounded-xl border border-line bg-surface px-2 text-xs text-fg"
                    value={rule.level}
                    onChange={(e) =>
                      updateRule(index, { level: e.target.value as BashRule["level"] })
                    }
                  >
                    <option value="auto">auto</option>
                    <option value="ask">ask</option>
                    <option value="deny">deny</option>
                  </select>
                  <button
                    onClick={() => removeRule(index)}
                    className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-faint active:bg-hover active:text-del"
                    aria-label="Remove rule"
                  >
                    <TrashIcon className="h-4 w-4" />
                  </button>
                </div>
              ))}
            </div>
          </section>

          <button className={btnPrimary} disabled={saving} onClick={() => void save()}>
            {saving ? <Spinner className="h-4 w-4" /> : null}
            Save settings
          </button>
        </>
      ) : null}
    </div>
  );
}
