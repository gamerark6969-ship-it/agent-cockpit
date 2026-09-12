import { useEffect, useState } from "react";
import { getArtifactObjectUrl } from "../lib/api";
import { CodeIcon, DownloadIcon, FileIcon } from "./Icons";
import Spinner from "./Spinner";

export interface FilePreviewProps {
  artifactId: string;
  filename: string;
  mime?: string;
  title?: string;
}

const TEXT_EXT =
  /\.(txt|md|markdown|json|js|mjs|cjs|ts|tsx|jsx|css|scss|less|html|htm|xml|yml|yaml|py|rb|go|rs|java|kt|c|h|cpp|hpp|cs|php|sh|bash|zsh|sql|toml|ini|cfg|conf|env|log|csv|tsv|srt|vtt)$/i;

function resolveKind(mime: string, filename: string): "html" | "image" | "pdf" | "text" | "other" {
  const lower = filename.toLowerCase();
  if (mime === "text/html" || lower.endsWith(".html") || lower.endsWith(".htm")) return "html";
  if (mime.startsWith("image/") || /\.(png|jpe?g|gif|webp|svg|bmp|avif|ico)$/i.test(lower))
    return "image";
  if (mime === "application/pdf" || lower.endsWith(".pdf")) return "pdf";
  if (mime.startsWith("text/") || mime === "application/json" || mime === "application/javascript")
    return "text";
  if (TEXT_EXT.test(lower)) return "text";
  return "other";
}

export default function FilePreview({ artifactId, filename, mime = "", title }: FilePreviewProps) {
  const [url, setUrl] = useState("");
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const kind = resolveKind(mime, filename);

  useEffect(() => {
    let cancelled = false;
    let created = "";
    getArtifactObjectUrl(artifactId)
      .then((objectUrl) => {
        created = objectUrl;
        if (cancelled) {
          URL.revokeObjectURL(objectUrl);
          return;
        }
        setUrl(objectUrl);
        if (kind === "text") {
          fetch(objectUrl)
            .then((r) => r.text())
            .then((t) => {
              if (!cancelled) setText(t);
            })
            .catch(() => undefined);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "failed to load file");
      });
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [artifactId, kind]);

  const open = () => {
    if (url) window.open(url, "_blank", "noopener,noreferrer");
  };

  const copy = async () => {
    if (text == null) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  };

  const header = (
    <div className="flex items-center gap-2 border-b border-zinc-800/80 bg-zinc-900/60 px-3 py-1.5">
      <FileIcon className="h-3.5 w-3.5 shrink-0 text-zinc-500" />
      <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-zinc-300">
        {title || filename}
      </span>
      {kind === "text" ? (
        <button onClick={() => void copy()} className="text-[11px] text-zinc-500 active:text-zinc-300">
          {copied ? "copied" : "copy"}
        </button>
      ) : null}
      <button onClick={open} className="text-[11px] text-emerald-400 active:text-emerald-300">
        open
      </button>
    </div>
  );

  if (error) {
    return (
      <div className="rounded-xl border border-red-900/60 bg-red-950/20 px-3 py-2">
        <p className="text-xs text-red-300">{error}</p>
      </div>
    );
  }

  if (!url) {
    return (
      <div className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900/40">
        {header}
        <div className="flex items-center justify-center gap-2 py-8 text-xs text-zinc-500">
          <Spinner className="h-4 w-4" /> loading {filename}…
        </div>
      </div>
    );
  }

  if (kind === "html") {
    return (
      <div className="overflow-hidden rounded-xl border border-zinc-800 bg-white">
        {header}
        <iframe
          title={title || filename}
          src={url}
          sandbox="allow-scripts allow-popups allow-forms"
          className="h-[60vh] max-h-[520px] min-h-[18rem] w-full bg-white"
        />
      </div>
    );
  }

  if (kind === "image") {
    return (
      <div className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900/40">
        {header}
        <button onClick={open} className="block w-full">
          <img src={url} alt={title || filename} className="max-h-80 w-full object-contain" />
        </button>
      </div>
    );
  }

  if (kind === "pdf") {
    return (
      <div className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900/40">
        {header}
        <iframe title={title || filename} src={url} className="h-[60vh] min-h-[18rem] w-full" />
      </div>
    );
  }

  if (kind === "text") {
    return (
      <div className="overflow-hidden rounded-xl border border-zinc-800 bg-black/60">
        {header}
        <div className="flex items-center gap-1.5 px-3 pt-2 text-[10px] uppercase tracking-wide text-zinc-600">
          <CodeIcon className="h-3.5 w-3.5" /> preview
        </div>
        <pre className="max-h-96 overflow-auto px-3 py-2 text-[11px] leading-relaxed text-zinc-300">
          {text ?? "loading…"}
        </pre>
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900/40">
      {header}
      <a
        href={url}
        download={filename}
        className="flex items-center justify-center gap-2 px-3 py-6 text-sm text-emerald-400 active:text-emerald-300"
      >
        <DownloadIcon className="h-4 w-4" /> Download {filename}
      </a>
    </div>
  );
}
