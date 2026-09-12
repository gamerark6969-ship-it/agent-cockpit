import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { decideApproval, listApprovals, type Approval } from "../lib/api";
import EmptyState from "../components/EmptyState";
import Spinner from "../components/Spinner";
import { CheckIcon, RefreshIcon, WarningIcon, XIcon } from "../components/Icons";
import { relativeTime } from "../lib/utils";
import { btnGhost, card } from "../lib/ui";

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
        <h1 className="text-xl font-bold text-zinc-100">Approvals</h1>
        <button
          onClick={() => void load()}
          className="flex h-10 w-10 items-center justify-center rounded-full border border-zinc-800 text-zinc-400 active:bg-zinc-800"
          aria-label="Refresh"
        >
          <RefreshIcon />
        </button>
      </div>

      {error ? (
        <p className="mb-3 rounded-lg border border-red-900/60 bg-red-950/20 px-3 py-2 text-xs text-red-300">
          {error}
        </p>
      ) : null}

      {loading ? (
        <div className="flex justify-center py-12 text-zinc-500">
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
            <div key={approval.id} className={`${card} border-amber-900/60 bg-amber-950/15`}>
              <div className="flex items-center gap-2">
                <WarningIcon className="h-4 w-4 text-amber-400" />
                <span className="rounded bg-amber-900/50 px-1.5 py-0.5 font-mono text-[10px] text-amber-300">
                  {approval.kind}
                </span>
                <span className="ml-auto text-[11px] text-zinc-500">
                  {relativeTime(approval.created_at)}
                </span>
              </div>
              <p className="mt-2 text-sm text-zinc-200">{approval.description}</p>
              <Link
                to={`/tasks/${approval.task_id}`}
                className="mt-1 inline-block text-[11px] text-emerald-400"
              >
                View task
              </Link>
              <div className="mt-3 flex gap-2">
                <button
                  disabled={busyId === approval.id}
                  onClick={() => void decide(approval.id, "approve")}
                  className="flex h-10 flex-1 items-center justify-center gap-1.5 rounded-lg bg-emerald-600 text-sm font-semibold text-white active:bg-emerald-700 disabled:opacity-50"
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
                  className="flex h-10 flex-1 items-center justify-center gap-1.5 rounded-lg bg-red-700 text-sm font-semibold text-white active:bg-red-800 disabled:opacity-50"
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
