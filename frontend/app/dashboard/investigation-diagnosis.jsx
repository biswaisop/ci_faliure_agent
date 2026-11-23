import { comparisonLabel, evidenceAnchor, inlineCodeParts, revealEvidence } from "./evidence-format";

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
        return (
          <li key={reference} className="max-w-full rounded-sm border border-line bg-background px-2 py-1 font-mono text-xs wrap-break-word">
            {available.has(reference) && !unsupported.has(reference) ? (
              <a href={`#${encodeURIComponent(id)}`} className="text-accent underline underline-offset-4" onClick={() => revealEvidence(id)}>{reference}</a>
            ) : <span className="text-muted">{reference} (unavailable)</span>}
          </li>
        );
      })}
    </ul>
  );
}

function InlineCode({ text }) {
  return inlineCodeParts(text).map((part, index) => index % 2 === 0 ? part : (
    <code key={index} className="rounded-sm bg-background px-1 font-mono text-[0.9em] text-foreground">{part}</code>
  ));
}

export function Diagnosis({ diagnosis, evidence, status }) {
  return (
    <section aria-labelledby="ai-heading" className="space-y-4 rounded-md border border-line bg-panel p-4 sm:p-5">
      <h2 id="ai-heading" className="text-lg font-semibold">AI diagnosis</h2>
      {status === "completed" && diagnosis ? (
        <>
          <p className="max-w-[70ch] leading-relaxed wrap-break-word whitespace-pre-wrap"><InlineCode text={diagnosis.summary} /></p>
          <div>
            <h3 className="text-sm font-semibold">Most likely root cause</h3>
            <p className="mt-1 max-w-[75ch] text-sm leading-relaxed wrap-break-word whitespace-pre-wrap text-muted"><InlineCode text={diagnosis.root_cause} /></p>
          </div>
          <div className="space-y-2 rounded-sm border border-line bg-background p-3">
            <dl className="grid gap-x-5 gap-y-2 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-xs text-muted">Failure mechanism confidence</dt>
                <dd className="mt-1 font-medium capitalize">{diagnosis.confidence.replaceAll("_", " ")}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted">Regression attribution</dt>
                <dd className="mt-1 font-medium capitalize">{(diagnosis.regression_attribution || "insufficient_evidence").replaceAll("_", " ")}</dd>
              </div>
            </dl>
            {evidence.comparison_status !== "available" && (
              <p className="text-xs text-muted">{comparisonLabel(evidence)}</p>
            )}
            {diagnosis.limitations?.length > 0 && (
              <ul aria-label="Investigation limitations" className="list-disc space-y-1 pl-5 text-xs wrap-break-word text-muted">
                {diagnosis.limitations.map((text, index) => <li key={index}><InlineCode text={text} /></li>)}
              </ul>
            )}
          </div>
          {diagnosis.suggested_actions.length > 0 && (
            <div>
              <h3 className="text-sm font-semibold">Suggested next actions</h3>
              <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm wrap-break-word whitespace-pre-wrap text-muted">
                {diagnosis.suggested_actions.map((action, index) => <li key={index}><InlineCode text={action} /></li>)}
              </ol>
            </div>
          )}
          <div className="space-y-2 border-t border-line pt-3">
            <h3 className="text-sm font-semibold">Evidence references</h3>
            <p className="text-sm text-muted">
              {diagnosis.evidence_validated ? "Reference IDs checked. A match confirms that an evidence item exists." : "Reference ID validation did not pass."}{" "}
              The diagnosis remains an AI-generated hypothesis.
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
