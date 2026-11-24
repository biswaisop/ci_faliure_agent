import { EvidenceCode } from "./evidence-code";

const fixStatus = {
  pending: "A fix suggestion is queued.",
  generating: "AutoPCI is inspecting source files for a possible fix.",
  insufficient_evidence: "The available evidence could not support a reliable fix.",
  failed: "A fix suggestion is unavailable for this attempt.",
};

const commentStatus = {
  pending: "PR comment queued",
  publishing: "Publishing PR comment",
  published: "PR comment posted",
  updated: "PR comment updated",
  no_pr_found: "No eligible pull request found",
  ambiguous_pr: "Pull request association is ambiguous",
  uncertain: "PR comment publication could not be confirmed",
  failed: "PR comment unavailable",
};

export function SuggestedFix({ fix, publication }) {
  const suggestion = fix?.suggestion;
  return (
    <section aria-labelledby="suggested-fix-heading" className="space-y-4 rounded-md border border-line bg-panel p-4 sm:p-5">
      <h2 id="suggested-fix-heading" className="text-lg font-semibold">{fix ? "Suggested fix" : "PR comment"}</h2>
      {fix?.status === "suggested" && suggestion ? (
        <>
          <p className="text-sm font-semibold text-accent">Not tested or applied</p>
          <p className="text-sm wrap-break-word">{suggestion.summary}</p>
          <p className="text-sm text-muted wrap-break-word">{suggestion.rationale}</p>
          {suggestion.changes.map((change) => (
            <div key={change.path} className="space-y-2">
              <h3 className="font-mono text-sm wrap-break-word">{change.path}</h3>
              <p className="text-sm text-muted wrap-break-word">{change.explanation}</p>
              <EvidenceCode kind="diff" text={[
                "@@ -1 +1 @@",
                ...change.original_snippet.split(/\r?\n/).map((line) => `-${line}`),
                ...change.replacement_snippet.split(/\r?\n/).map((line) => `+${line}`),
              ].join("\n")} />
            </div>
          ))}
          {[
            ["Risks", suggestion.risks],
            ["Suggested validation", suggestion.tests_to_run],
          ].map(([label, items]) => items.length > 0 && (
            <div key={label} className="space-y-1 text-sm">
              <h3 className="font-semibold">{label}</h3>
              <ul className="list-disc space-y-1 pl-5 text-muted">
                {items.map((item, index) => <li key={index} className="wrap-break-word">{item}</li>)}
              </ul>
            </div>
          ))}
        </>
      ) : fix && <p role="status" className="text-sm text-muted">{fixStatus[fix.status]}</p>}
      {publication && (
        <p className="flex flex-wrap gap-2 text-sm text-muted">
          <span>{commentStatus[publication.status] || "PR comment unavailable"}</span>
          {publication.comment_url && (
            <a href={publication.comment_url} className="text-accent underline underline-offset-4">Open comment in GitHub</a>
          )}
        </p>
      )}
    </section>
  );
}
