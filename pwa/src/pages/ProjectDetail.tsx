import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  createTask,
  getProject,
  listModels,
  listTasks,
  type Project,
  type Task,
} from "../lib/api";
import EmptyState from "../components/EmptyState";
import Spinner from "../components/Spinner";
import StatusBadge from "../components/StatusBadge";
import { PlusIcon } from "../components/Icons";
import { relativeTime } from "../lib/utils";
import { btnGhost, btnPrimary, card, inputBase, textareaBase } from "../lib/ui";

export default function ProjectDetail() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [project, setProject] = useState<Project | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [models, setModels] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("");
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      setError("");
      const [p, t] = await Promise.all([getProject(id), listTasks(id)]);
      setProject(p);
      setTasks(t);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to load project");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    listModels()
      .then((r) => setModels(r.models))
      .catch(() => setModels([]));
    const interval = window.setInterval(() => void load(), 15000);
    return () => window.clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const submit = async () => {
    if (!prompt.trim()) {
      setError("Describe the task for the agent.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const task = await createTask(id, {
        prompt: prompt.trim(),
        model: model.trim() || undefined,
      });
      setPrompt("");
      setShowForm(false);
      navigate(`/tasks/${task.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to create task");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-16 text-zinc-500">
        <Spinner />
      </div>
    );
  }

  if (!project) {
    return (
      <div className="px-4 pt-6">
        <p className="text-sm text-red-400">{error || "project not found"}</p>
      </div>
    );
  }

  return (
    <div className="safe-top px-4 pt-4">
      <Link to="/projects" className="mb-3 inline-block text-xs text-zinc-500">
        ← Projects
      </Link>
      <div className="mb-4 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="truncate text-xl font-bold text-zinc-100">{project.name}</h1>
          <p className="mt-0.5 truncate text-xs text-zinc-500">{project.repo_url}</p>
        </div>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-emerald-600 text-white active:bg-emerald-700"
          aria-label="New task"
        >
          <PlusIcon />
        </button>
      </div>

      {showForm ? (
        <div className={`${card} mb-4`}>
          <p className="mb-3 text-sm font-semibold text-zinc-200">Delegate a task</p>
          <textarea
            className={`${textareaBase} min-h-28`}
            placeholder="e.g. Add Google OAuth to the API and cover it with tests"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
          />
          <div className="mt-2.5">
            <label className="mb-1 block text-[11px] text-zinc-500">Model</label>
            {models.length > 0 ? (
              <select
                className={inputBase}
                value={model}
                onChange={(e) => setModel(e.target.value)}
              >
                <option value="">Use server default</option>
                {models.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            ) : (
              <input
                className={inputBase}
                placeholder="Use server default"
                value={model}
                onChange={(e) => setModel(e.target.value)}
              />
            )}
          </div>
          <div className="mt-3 flex gap-2">
            <button className={btnPrimary} disabled={saving} onClick={submit}>
              {saving ? <Spinner className="h-4 w-4" /> : null}
              Start task
            </button>
            <button className={btnGhost} onClick={() => setShowForm(false)}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {error ? (
        <p className="mb-3 rounded-lg border border-red-900/60 bg-red-950/20 px-3 py-2 text-xs text-red-300">
          {error}
        </p>
      ) : null}

      <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-zinc-500">Tasks</h2>
      {tasks.length === 0 ? (
        <EmptyState
          title="No tasks yet"
          description="Tap + to hand the agent its first job in this repository."
        />
      ) : (
        <div className="flex flex-col gap-2.5">
          {[...tasks].reverse().map((task) => (
            <Link key={task.id} to={`/tasks/${task.id}`} className={`${card} block`}>
              <div className="flex items-start justify-between gap-3">
                <p className="line-clamp-2 min-w-0 flex-1 text-sm text-zinc-200">{task.prompt}</p>
                <StatusBadge status={task.status} compact />
              </div>
              <div className="mt-2 flex items-center gap-3 text-[11px] text-zinc-600">
                <span>{relativeTime(task.created_at)}</span>
                <span>{task.iterations} iters</span>
                {task.model ? <span className="truncate">{task.model}</span> : null}
              </div>
              {task.error ? (
                <p className="mt-1.5 line-clamp-2 text-[11px] text-red-400">{task.error}</p>
              ) : null}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
