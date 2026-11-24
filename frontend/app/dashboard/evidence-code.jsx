import { useMemo, useState } from "react";
import { diffLines, logLineTone } from "./evidence-format";

const previewLines = 80;
const tones = {
  trace: "border-slate-400/35 bg-slate-400/10 text-sky-200",
  error: "border-failure/50 bg-failure/10 text-failure",
  removed: "border-failure/50 bg-failure/10 text-failure",
  added: "border-accent/50 bg-accent/10 text-accent",
  context: "border-transparent text-foreground",
};

export function EvidenceCode({ text, kind, full = false }) {
  const [expanded, setExpanded] = useState(false);
  const [copyStatus, setCopyStatus] = useState("");
  const lines = useMemo(() => kind === "diff" ? diffLines(text) : text.split(/\r\n?|\n/), [text, kind]);
  const slice = full || expanded ? lines : lines.slice(0, previewLines);
  const visible = kind === "diff" ? slice : slice.map((line, index) => ({
    line, tone: logLineTone(line), oldLine: index + 1,
  }));

  async function copyLog() {
    try {
      await navigator.clipboard.writeText(text);
      setCopyStatus("Log copied.");
    } catch {
      setCopyStatus("Couldn’t copy the log. Select the text to copy it manually.");
    }
  }

  return (
    <div className="min-w-0 overflow-hidden rounded-sm border border-line bg-background">
      {kind === "log" && (
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-2 text-xs">
          <span>{full ? `Full job log · ${lines.length} lines` : `${lines.length} excerpt lines`}</span>
          <button type="button" className="rounded-sm px-2 py-1 font-medium text-accent hover:bg-accent-soft" onClick={copyLog}>Copy log</button>
          {copyStatus && <p className="w-full" role="status">{copyStatus}</p>}
        </div>
      )}
      <pre aria-label={kind === "diff" ? "Unified diff" : full ? "Full job log" : "Reduced job log"} tabIndex={0} className={`${full ? "max-h-[60vh]" : "max-h-96"} overflow-auto py-1 font-mono text-xs leading-5 whitespace-pre`}>
        <code className="block min-w-full w-max">
          {visible.map(({ line, tone, oldLine, newLine }, index) => (
            <span key={index} className={`block min-h-5 border-l-2 pr-3 ${tones[tone]}`}>
              <span aria-hidden="true" className="inline-block w-12 pr-2 text-right text-muted select-none">{oldLine}</span>
              {kind === "diff" && <span aria-hidden="true" className="inline-block w-10 pr-2 text-right text-muted select-none">{newLine}</span>}
              {line}
            </span>
          ))}
        </code>
      </pre>
      {!full && lines.length > previewLines && (
        <button type="button" aria-expanded={expanded} className="w-full border-t border-line px-3 py-2 text-left text-xs font-medium text-accent hover:bg-accent-soft" onClick={() => setExpanded(!expanded)}>
          {expanded ? "Show fewer lines" : `Show all ${lines.length} lines`}
        </button>
      )}
    </div>
  );
}
