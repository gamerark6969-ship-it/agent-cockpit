import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { decideApproval, listApprovals, type Approval } from "../lib/api";
import EmptyState from "../components/EmptyState";
import Spinner from "../components/Spinner";
import { CheckIcon, RefreshIcon, WarningIcon, XIcon } from "../components/Icons";
import { relativeTime } from "../lib/utils";

export default function Approvals() {
  const [rows, setRows] = useState<Approval[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");

  const load = async () => {
    try {
      setError("");
      const data = await listApprovals(true);
      setRows(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to load approvals");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    const interval = window.setInterval(() => void load(), 15000);
    return () => window.clearInterval(interval);
  }, []);

  const decide = async (id: string, decision: "approve" | "deny") => {
    setBusyId(id);
    setError("");
    try {
      await decideApproval(id, decision);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to submit decision");
    } finally {
      setBusyId("");
    }
  };

  return (
    <div className="safe-top px-4 pt-4">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-semibold tracking-tight text-fg">Approvals</h1>
        <button
          onClick={() => void load()}
          className="flex h-10 w-10 items-center justify-center rounded-xl border border-line bg-surface text-muted active:bg-hover"
          aria-label="Refresh"
        >
          <RefreshIcon />
        </button>
      </div>

      {error ? (
        <p className="mb-3 rounded-lg border border-red-950 bg-red-950/25 px-3 py-2 text-xs text-del">
          {error}
        </p>
      ) : null}

      {loading ? (
        <div className="flex justify-center py-12 text-faint">
          <Spinner />
        </div>
      ) : rows.length === 0 ? (
        <EmptyState
          title="No pending approvals"
          description="When the agent needs permission for a risky action, it shows up here."
        />
      ) : (
        <div className="flex flex-col gap-2.5">
          {rows.map((approval) => (
            <div key={approval.id} className="rounded-2xl border border-warn/35 bg-warn/10 px-4 py-3.5">
              <div className="flex items-center gap-2">
                <WarningIcon className="h-4 w-4 text-warn" />
                <span className="rounded bg-warn/15 px-1.5 py-0.5 font-mono text-[10px] text-warn">
                  {approval.kind}
                </span>
                <span className="ml-auto text-[11px] text-faint">
                  {relativeTime(approval.created_at)}
                </span>
              </div>
              <p className="mt-2 text-sm text-fg">{approval.description}</p>
              <Link
                to={`/tasks/${approval.task_id}`}
                className="mt-1 inline-block text-[11px] text-muted underline underline-offset-2"
              >
                View task
              </Link>
              <div className="mt-3 flex gap-2">
                <button
                  disabled={busyId === approval.id}
                  onClick={() => void decide(approval.id, "approve")}
                  className="flex h-10 flex-1 items-center justify-center gap-1.5 rounded-lg bg-accent text-sm font-semibold text-canvas active:bg-white disabled:opacity-40"
                >
                  {busyId === approval.id ? (
                    <Spinner className="h-4 w-4" />
                  ) : (
                    <CheckIcon className="h-4 w-4" />
                  )}
                  Approve
                </button>
                <button
                  disabled={busyId === approval.id}
                  onClick={() => void decide(approval.id, "deny")}
                  className="flex h-10 flex-1 items-center justify-center gap-1.5 rounded-lg border border-red-950 bg-red-950/30 text-sm font-semibold text-del active:bg-red-950/60 disabled:opacity-40"
                >
                  <XIcon className="h-4 w-4" />
                  Deny
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
