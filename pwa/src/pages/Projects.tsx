import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { createProject, deleteProject, listProjects, type Project } from "../lib/api";
import EmptyState from "../components/EmptyState";
import Spinner from "../components/Spinner";
import { PlusIcon, TrashIcon } from "../components/Icons";
import { btnGhost, btnPrimary, card, inputBase } from "../lib/ui";

export default function Projects() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [repoUrl, setRepoUrl] = useState("");
  const [name, setName] = useState("");
  const [branch, setBranch] = useState("");
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      setError("");
      const rows = await listProjects();
      setProjects(rows);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to load projects");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const submit = async () => {
    if (!repoUrl.trim()) {
      setError("Repository URL is required.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await createProject({
        repo_url: repoUrl.trim(),
        name: name.trim() || undefined,
        default_branch: branch.trim() || undefined,
      });
      setRepoUrl("");
      setName("");
      setBranch("");
      setShowForm(false);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to create project");
    } finally {
      setSaving(false);
    }
  };

  const remove = async (project: Project) => {
    if (!window.confirm(`Delete "${project.name}" and stop its tasks?`)) return;
    try {
      await deleteProject(project.id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to delete project");
    }
  };

  return (
    <div className="safe-top px-4 pt-4">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-semibold tracking-tight text-fg">Projects</h1>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="flex h-10 w-10 items-center justify-center rounded-xl border border-line bg-surface text-muted active:bg-hover"
          aria-label="New project"
        >
          <PlusIcon />
        </button>
      </div>

      {showForm ? (
        <div className={`${card} mb-4`}>
          <p className="mb-3 text-sm font-semibold text-fg">New project</p>
          <div className="flex flex-col gap-2.5">
            <input
              className={inputBase}
              placeholder="https://github.com/owner/repo"
              value={repoUrl}
              onChange={(e) => setRepoUrl(e.target.value)}
            />
            <input
              className={inputBase}
              placeholder="Name (optional)"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <input
              className={inputBase}
              placeholder="Default branch (optional, default main)"
              value={branch}
              onChange={(e) => setBranch(e.target.value)}
            />
            <div className="flex gap-2">
              <button className={btnPrimary} disabled={saving} onClick={submit}>
                {saving ? <Spinner className="h-4 w-4" /> : null}
                Create
              </button>
              <button className={btnGhost} onClick={() => setShowForm(false)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {error ? (
        <p className="mb-3 rounded-lg border border-red-950 bg-red-950/25 px-3 py-2 text-xs text-del">
          {error}
        </p>
      ) : null}

      {loading ? (
        <div className="flex justify-center py-12 text-faint">
          <Spinner />
        </div>
      ) : projects.length === 0 ? (
        <EmptyState
          title="No projects yet"
          description="Connect a GitHub repository to let the agent start working on it."
        />
      ) : (
        <div className="flex flex-col gap-2.5">
          {projects.map((project) => (
            <div key={project.id} className={card}>
              <div className="flex items-start gap-3">
                <button
                  className="min-w-0 flex-1 text-left"
                  onClick={() => navigate(`/projects/${project.id}`)}
                >
                  <p className="truncate text-sm font-semibold text-fg">{project.name}</p>
                  <p className="mt-0.5 truncate text-xs text-faint">{project.repo_url}</p>
                  <p className="mt-1 font-mono text-[11px] text-faint">
                    branch {project.default_branch}
                  </p>
                </button>
                <button
                  onClick={() => void remove(project)}
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-faint active:bg-hover active:text-del"
                  aria-label="Delete project"
                >
                  <TrashIcon className="h-4 w-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
