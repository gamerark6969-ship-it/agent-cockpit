import type { ReactNode } from "react";

const FENCE = /```([a-zA-Z0-9_+-]*)\n?([\s\S]*?)```/g;

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const rx = /(`[^`\n]+`)|(https?:\/\/[^\s)]+)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = rx.exec(text))) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    if (m[1]) {
      nodes.push(
        <code
          key={`${keyPrefix}-c${i}`}
          className="rounded bg-black/50 px-1 py-0.5 font-mono text-[0.85em] text-emerald-300"
        >
          {m[1].slice(1, -1)}
        </code>,
      );
    } else if (m[2]) {
      nodes.push(
        <a
          key={`${keyPrefix}-a${i}`}
          href={m[2]}
          target="_blank"
          rel="noreferrer"
          className="break-all text-emerald-400 underline"
        >
          {m[2]}
        </a>,
      );
    }
    last = m.index + m[0].length;
    i++;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function renderPlain(block: string, keyPrefix: string): ReactNode[] {
  const lines = block.split("\n");
  const out: ReactNode[] = [];
  let list: string[] = [];
  let k = 0;
  const flush = () => {
    if (!list.length) return;
    out.push(
      <ul key={`ul-${keyPrefix}-${k++}`} className="my-1 list-disc space-y-0.5 pl-5">
        {list.map((li, idx) => (
          <li key={idx}>{renderInline(li, `${keyPrefix}-${k}-${idx}`)}</li>
        ))}
      </ul>,
    );
    list = [];
  };
  lines.forEach((line, idx) => {
    const t = line.replace(/\s+$/, "");
    if (/^\s*[-*]\s+/.test(t)) {
      list.push(t.replace(/^\s*[-*]\s+/, ""));
      return;
    }
    flush();
    if (!t.trim()) {
      out.push(<div key={`sp-${keyPrefix}-${idx}`} className="h-2" />);
      return;
    }
    out.push(
      <p key={`p-${keyPrefix}-${idx}`} className="whitespace-pre-wrap">
        {renderInline(t, `${keyPrefix}-p-${idx}`)}
      </p>,
    );
  });
  flush();
  return out;
}

export default function Markdown({ text, className = "" }: { text: string; className?: string }) {
  if (!text) return null;
  const blocks: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  FENCE.lastIndex = 0;
  while ((m = FENCE.exec(text))) {
    if (m.index > last) {
      blocks.push(
        <div key={`t${i}`} className="space-y-0.5">
          {renderPlain(text.slice(last, m.index), `t${i}`)}
        </div>,
      );
    }
    blocks.push(
      <pre
        key={`cb${i}`}
        className="my-2 overflow-x-auto rounded-lg border border-zinc-800 bg-black/60 p-2.5 text-[11px] leading-relaxed text-zinc-300"
      >
        <code>{m[2].replace(/\n$/, "")}</code>
      </pre>,
    );
    last = m.index + m[0].length;
    i++;
  }
  if (last < text.length) {
    blocks.push(
      <div key="t-end" className="space-y-0.5">
        {renderPlain(text.slice(last), "t-end")}
      </div>,
    );
  }
  return <div className={`text-sm leading-relaxed text-zinc-200 ${className}`}>{blocks}</div>;
}
