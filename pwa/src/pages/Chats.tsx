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
    <div className="safe-top px-4 pb-nav pt-6">
      <header className="mb-5 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-fg">Chats</h1>
          <p className="mt-0.5 text-[13px] text-faint">Your autonomous agent, on tap</p>
        </div>
        <button
          onClick={() => void newChat()}
          disabled={creating}
          className="flex h-10 w-10 items-center justify-center rounded-xl border border-line bg-surface text-muted transition-colors active:bg-hover disabled:opacity-40"
          aria-label="New chat"
        >
          {creating ? <Spinner className="h-4 w-4" /> : <PlusIcon />}
        </button>
      </header>

      {error ? (
        <p className="animate-fade-in mb-3 rounded-xl border border-red-950 bg-red-950/30 px-3.5 py-2.5 text-xs text-del">
          {error}
        </p>
      ) : null}

      {loading ? (
        <div className="flex justify-center py-16 text-faint">
          <Spinner />
        </div>
      ) : chats.length === 0 ? (
        <EmptyState
          icon={<ChatIcon />}
          title="No chats yet"
          description="Tap + to start a conversation. Ask the agent to search, read email, or work on GitHub."
        />
      ) : (
        <div className="overflow-hidden rounded-2xl border border-line bg-surface">
          {chats.map((project, i) => (
            <div
              key={project.id}
              className={`flex items-center gap-3 px-3.5 py-3 ${
                i > 0 ? "border-t border-line" : ""
              }`}
            >
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-elevated text-muted">
                <ChatIcon className="h-[18px] w-[18px]" />
              </div>
              <Link to={`/c/${project.id}`} className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-fg">{project.name}</p>
                <p className="mt-0.5 text-[11px] text-faint">Tap to continue</p>
              </Link>
              <button
                onClick={() => void remove(project)}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-faint transition-colors active:bg-hover active:text-del"
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
        className="mt-3 flex items-center gap-3 rounded-2xl border border-line bg-surface px-3.5 py-3 transition-colors active:bg-hover"
      >
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-elevated text-muted">
          <RepoIcon className="h-[18px] w-[18px]" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-fg">Code workspaces</p>
          <p className="mt-0.5 text-[11px] text-faint">Repo-scoped tasks and pull requests</p>
        </div>
        <ChevronIcon className="h-4 w-4 text-faint" />
      </Link>
    </div>
  );
}
