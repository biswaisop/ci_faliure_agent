import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import { apiFetch } from "../../lib/api.js";
import { comparisonLabel, evidenceAnchor, revealEvidence } from "./evidence-format";

const investigationMessages = {
  pending: "The AI investigation is queued.",
  running: "The AI is analyzing the collected evidence.",
  completed: "The investigation completed, but its diagnosis is unavailable.",
  failed: "The AI investigation couldn’t finish. You can still inspect the available evidence below.",
  unavailable: "There is no AI investigation recorded for this attempt.",
};

function Citations({ diagnosis, evidence }) {
  const available = new Set([
    ...evidence.failure_packets.map((packet) => `log:${packet.job_id}`),
    ...evidence.changed_files.map((file) => `diff:${file.filename}`),
  ]);
  const unsupported = new Set(diagnosis.unsupported_evidence_ids);
  const references = new Set([...diagnosis.evidence_ids, ...unsupported]);
  if (!references.size) return <p className="text-sm text-muted">No evidence references were supplied.</p>;

  return (
    <ul aria-label="Evidence citations" className="flex flex-wrap gap-2">
      {[...references].map((reference) => {
        const id = evidenceAnchor(reference);
        const inspectedSource = !unsupported.has(reference) && /^(source|workflow):/.test(reference);
        return (
          <li key={reference} className="max-w-full rounded-sm border border-line bg-background px-2 py-1 font-mono text-xs wrap-break-word">
            {available.has(reference) && !unsupported.has(reference) ? (
              <a href={`#${encodeURIComponent(id)}`} className="text-accent underline underline-offset-4" onClick={() => revealEvidence(id)}>{reference}</a>
            ) : <span className="text-muted">{reference} ({inspectedSource ? "inspected source" : "unavailable"})</span>}
          </li>
        );
      })}
    </ul>
  );
}

function ReportLink({ href, children }) {
  if (!href?.startsWith("#evidence-")) return children;
  let id;
  try {
    id = decodeURIComponent(href.slice(1));
  } catch {
    return children;
  }
  return <a href={href} className="text-accent underline underline-offset-4" onClick={() => revealEvidence(id)}>{children}</a>;
}

export function ReportMarkdown({ text }) {
  return (
    <div className="max-w-[75ch] space-y-2 wrap-break-word text-sm leading-relaxed text-muted [&_h3]:mt-4 [&_h3]:font-semibold [&_h3]:text-foreground [&_ul]:list-disc [&_ol]:list-decimal [&_ul]:pl-5 [&_ol]:pl-5 [&_code]:font-mono [&_code]:text-foreground [&_pre]:overflow-x-auto [&_pre]:rounded-sm [&_pre]:bg-background [&_pre]:p-3">
      <Markdown disallowedElements={["img"]} components={{ a: ReportLink }}>{text}</Markdown>
    </div>
  );
}

export async function readMarkdown(body, onText) {
  const reader = body.pipeThrough(new TextDecoderStream()).getReader();
  let text = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) return;
      text += value;
      onText(text);
    }
  } finally {
    reader.releaseLock();
  }
}

function StreamedReport({ runPath, getToken }) {
  const [attempt, setAttempt] = useState(0);
  const [report, setReport] = useState(null);
  const current = report?.attempt === attempt ? report : null;

  useEffect(() => {
    const controller = new AbortController();
    let text = "";

    async function load() {
      try {
        const response = await apiFetch(getToken, `/api/github/repositories${runPath}/report`, {
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
        });
        if (!response.ok || !response.body) throw new Error("Report stream unavailable");
        await readMarkdown(response.body, (content) => {
          text = content;
          if (!controller.signal.aborted) setReport({ attempt, text, status: "streaming" });
        });
        if (!text) throw new Error("Report stream was empty");
        if (!controller.signal.aborted) setReport({ attempt, text, status: "complete" });
      } catch {
        if (!controller.signal.aborted) setReport({ attempt, text, status: "failed" });
      }
    }

    load();
    return () => controller.abort();
  }, [attempt, getToken, runPath]);

  return (
    <div aria-busy={!current || current.status === "streaming"}>
      {current?.text && <ReportMarkdown text={current.text} />}
      {!current?.text && current?.status !== "failed" && <p role="status" className="text-sm text-muted">Loading investigation report…</p>}
      {current?.status === "failed" && (
        <p role="alert" className="text-sm text-failure">
          Couldn’t load the full report.{" "}
          <button type="button" className="text-accent underline" onClick={() => setAttempt((value) => value + 1)}>Retry</button>
        </p>
      )}
    </div>
  );
}

export function Diagnosis({ diagnosis, evidence, status, runPath, getToken }) {
  return (
    <section aria-labelledby="ai-heading" className="space-y-4 rounded-md border border-line bg-panel p-4 sm:p-5">
      <h2 id="ai-heading" className="text-lg font-semibold">AI diagnosis</h2>
      {status === "completed" && diagnosis ? (
        <>
          <StreamedReport runPath={runPath} getToken={getToken} />
          {evidence.comparison_status !== "available" && (
            <p className="text-xs text-muted">{comparisonLabel(evidence)}</p>
          )}
          <div className="space-y-2 border-t border-line pt-3">
            <h3 className="text-sm font-semibold">Evidence references</h3>
            <p className="text-sm text-muted">
              {diagnosis.evidence_validated ? "Reference IDs checked. A match confirms that an evidence item exists." : "Reference ID validation did not pass."}
            </p>
            <Citations diagnosis={diagnosis} evidence={evidence} />
          </div>
        </>
      ) : <p role="status" className={`text-sm ${status === "failed" ? "text-failure" : "text-muted"}`}>
        {investigationMessages[status] || investigationMessages.unavailable}
      </p>}
    </section>
  );
}
