export default function PatchView({ patch }: { patch?: string }) {
  if (!patch) return null;
  return <details className="mt-4 rounded-xl border border-line bg-elevated p-3" open>
    <summary className="cursor-pointer text-sm font-medium">Code changes</summary>
    <pre className="mt-3 max-h-[32rem] overflow-auto text-[11px] leading-5"><code>
      {patch.split("\n").map((line,index) => <span key={index} className={`block ${line.startsWith("+") ? "text-emerald-300" : line.startsWith("-") ? "text-red-300" : line.startsWith("@@") ? "text-blue-300" : "text-muted"}`}>{line || " "}</span>)}
    </code></pre>
  </details>;
}
