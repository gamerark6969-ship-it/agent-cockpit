import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { createProject, deleteProject, listProjects, type Project } from "../lib/api";
import EmptyState from "../components/EmptyState";
import Spinner from "../components/Spinner";
import { ChatIcon, PlusIcon, RepoIcon, TrashIcon } from "../components/Icons";
import { card } from "../lib/ui";

export default function Chats() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  const load = async () => {
    try {
      setError("");
      setProjects(await listProjects());
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to load chats");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const newChat = async () => {
    setCreating(true);
    setError("");
    try {
      const project = await createProject({ kind: "chat" });
      navigate(`/c/${project.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to start chat");
      setCreating(false);
    }
  };

  const remove = async (project: Project) => {
    if (!window.confirm(`Delete "${project.name}"?`)) return;
    try {
      await deleteProject(project.id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to delete chat");
    }
  };

  const chats = projects.filter((p) => p.kind !== "repo");

  return (
    <div className="safe-top px-4 pt-4">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ChatIcon className="h-5 w-5 text-emerald-400" />
          <h1 className="text-xl font-bold text-zinc-100">Chats</h1>
        </div>
        <button
          onClick={() => void newChat()}
          disabled={creating}
          className="flex h-10 w-10 items-center justify-center rounded-full bg-emerald-600 text-white active:bg-emerald-700 disabled:opacity-50"
          aria-label="New chat"
        >
          {creating ? <Spinner className="h-4 w-4" /> : <PlusIcon />}
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
      ) : chats.length === 0 ? (
        <EmptyState
          title="No chats yet"
          description="Tap + to start a conversation. Ask the agent to search, read email, or work on GitHub."
        />
      ) : (
        <div className="flex flex-col gap-2.5">
          {chats.map((project) => (
            <div key={project.id} className={`${card} flex items-center gap-3`}>
              <Link to={`/c/${project.id}`} className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold text-zinc-100">{project.name}</p>
                <p className="mt-0.5 text-[11px] text-zinc-600">tap to continue</p>
              </Link>
              <button
                onClick={() => void remove(project)}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-zinc-600 active:bg-zinc-800 active:text-red-400"
                aria-label="Delete chat"
              >
                <TrashIcon className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>
      )}

      <Link
        to="/projects"
        className="mt-4 flex items-center gap-2.5 rounded-2xl border border-zinc-800 bg-zinc-900/40 px-4 py-3.5"
      >
        <RepoIcon className="h-5 w-5 text-zinc-500" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-zinc-200">Code workspaces</p>
          <p className="text-[11px] text-zinc-600">Repo-scoped tasks and pull requests</p>
        </div>
        <span className="text-zinc-600">→</span>
      </Link>
    </div>
  );
}
