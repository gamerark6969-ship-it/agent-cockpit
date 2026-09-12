import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { createProject, deleteProject, listProjects, type Project } from "../lib/api";
import EmptyState from "../components/EmptyState";
import Spinner from "../components/Spinner";
import { ChatIcon, ChevronIcon, PlusIcon, RepoIcon, TrashIcon } from "../components/Icons";

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
    <div className="safe-top px-4 pb-nav pt-5">
      <header className="mb-5 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-zinc-50">Chats</h1>
          <p className="mt-0.5 text-xs text-zinc-500">Your autonomous agent, on tap</p>
        </div>
        <button
          onClick={() => void newChat()}
          disabled={creating}
          className="flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-b from-emerald-500 to-emerald-600 text-white shadow-lg shadow-emerald-950/40 transition-transform active:scale-95 disabled:opacity-50"
          aria-label="New chat"
        >
          {creating ? <Spinner className="h-4 w-4" /> : <PlusIcon />}
        </button>
      </header>

      {error ? (
        <p className="animate-fade-in mb-3 rounded-2xl border border-red-900/60 bg-red-950/20 px-4 py-2.5 text-xs text-red-300">
          {error}
        </p>
      ) : null}

      {loading ? (
        <div className="flex justify-center py-16 text-zinc-500">
          <Spinner />
        </div>
      ) : chats.length === 0 ? (
        <EmptyState
          icon={<ChatIcon />}
          title="No chats yet"
          description="Tap + to start a conversation. Ask the agent to search, read email, or work on GitHub."
        />
      ) : (
        <div className="flex flex-col gap-2.5">
          {chats.map((project, i) => (
            <div
              key={project.id}
              className="animate-rise flex items-center gap-3 rounded-2xl border border-zinc-800/80 bg-zinc-900/50 px-3.5 py-3 backdrop-blur-sm"
              style={{ animationDelay: `${Math.min(i, 8) * 30}ms` }}
            >
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-emerald-500/10 text-emerald-400 ring-1 ring-emerald-500/20">
                <ChatIcon className="h-5 w-5" />
              </div>
              <Link to={`/c/${project.id}`} className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold text-zinc-100">{project.name}</p>
                <p className="mt-0.5 text-[11px] text-zinc-500">Tap to continue</p>
              </Link>
              <button
                onClick={() => void remove(project)}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-zinc-600 transition-colors active:bg-zinc-800 active:text-red-400"
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
        className="mt-4 flex items-center gap-3 rounded-2xl border border-zinc-800/80 bg-zinc-900/40 px-4 py-3.5 transition-colors active:bg-zinc-900"
      >
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-zinc-800/60 text-zinc-400">
          <RepoIcon className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-zinc-200">Code workspaces</p>
          <p className="text-[11px] text-zinc-500">Repo-scoped tasks and pull requests</p>
        </div>
        <ChevronIcon className="h-4 w-4 text-zinc-600" />
      </Link>
    </div>
  );
}
