import { useMemo, useState } from "react";
import { diffLines, logLineTone } from "./evidence-format";

const previewLines = 80;
const pageSize = 500;
const tones = {
  trace: "border-slate-400/35 bg-slate-400/10 text-sky-200",
  error: "border-failure/50 bg-failure/10 text-failure",
  removed: "border-failure/50 bg-failure/10 text-failure",
  added: "border-accent/50 bg-accent/10 text-accent",
  context: "border-transparent text-foreground",
};

export function EvidenceCode({ text, kind, full = false }) {
  const [expanded, setExpanded] = useState(false);
  const [page, setPage] = useState(0);
  const [copyStatus, setCopyStatus] = useState("");
  const lines = useMemo(() => kind === "diff" ? diffLines(text) : text.split("\n"), [text, kind]);
  const start = full ? page * pageSize : 0;
  const end = full ? start + pageSize : expanded ? lines.length : previewLines;
  const slice = lines.slice(start, end);
  const visible = kind === "diff" ? slice : slice.map((line, index) => ({
    line, tone: logLineTone(line), oldLine: start + index + 1,
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
      <pre key={page} aria-label={kind === "diff" ? "Unified diff" : full ? "Full job log" : "Reduced job log"} tabIndex={0} className="max-h-96 overflow-auto py-1 font-mono text-xs leading-5 whitespace-pre">
        <code className="block min-w-full w-max">
          {visible.map(({ line, tone, oldLine, newLine }, index) => (
            <span key={index} className={`inline-block min-w-full border-l-2 pr-3 ${tones[tone]}`}>
              <span aria-hidden="true" className="inline-block w-12 pr-2 text-right text-muted select-none">{oldLine}</span>
              {kind === "diff" && <span aria-hidden="true" className="inline-block w-10 pr-2 text-right text-muted select-none">{newLine}</span>}
              {line}
              {index + 1 < visible.length ? "\n" : ""}
            </span>
          ))}
        </code>
      </pre>
      {full && lines.length > pageSize && (
        <nav aria-label="Log pages" className="flex flex-wrap items-center gap-3 border-t border-line px-3 py-2 text-xs">
          <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)} className="text-accent disabled:text-muted">Previous lines</button>
          <span role="status">Lines {start + 1}–{Math.min(end, lines.length)} of {lines.length}</span>
          <button type="button" disabled={end >= lines.length} onClick={() => setPage(page + 1)} className="text-accent disabled:text-muted">Next lines</button>
        </nav>
      )}
      {!full && lines.length > previewLines && (
        <button type="button" aria-expanded={expanded} className="w-full border-t border-line px-3 py-2 text-left text-xs font-medium text-accent hover:bg-accent-soft" onClick={() => setExpanded(!expanded)}>
          {expanded ? "Show fewer lines" : `Show all ${lines.length} lines`}
        </button>
      )}
    </div>
  );
}
