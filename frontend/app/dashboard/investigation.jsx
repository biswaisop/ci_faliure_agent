"use client";

import { ArrowUpRight } from "lucide-react";
import Link from "next/link";
import { EvidenceExplorer, evidenceAnchor } from "./investigation-evidence";
import { repositoryPath, useWorkflowData } from "./workflow-data";
import { categoryLabels, RequestStatus, RunStatuses, workflowName, workspacePanel } from "./workflow-ui";

const collectionMessages = {
  pending: "Evidence collection is queued.",
  collecting: "Collecting failed jobs, reduced logs, and commit evidence.",
  completed: "Evidence collection is complete. Some sources may have been unavailable.",
  failed: "We couldn’t finish collecting evidence. Check the workflow in GitHub Actions for the original logs.",
};

const investigationMessages = {
  pending: "The AI investigation is queued.",
  running: "The AI is analyzing the collected evidence.",
  completed: "The investigation completed, but its diagnosis is unavailable.",
  failed: "The AI investigation couldn’t finish. You can still inspect the available evidence below.",
  unavailable: "There is no AI investigation recorded for this attempt.",
};

function githubRunUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.origin === "https://github.com" && !url.username && !url.password
      && /^\/[^/]+\/[^/]+\/actions\/runs\/\d+(?:\/attempts\/\d+)?\/?$/.test(url.pathname)) return url.href;
  } catch {
    return null;
  }
  return null;
}

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
              <a href={`#${encodeURIComponent(id)}`} className="text-accent underline underline-offset-4" onClick={() => {
                const target = document.getElementById(id);
                if (target) {
                  target.open = true;
                  target.querySelector("summary")?.focus({ preventScroll: true });
                }
              }}>{reference}</a>
            ) : <span className="text-muted">{reference} (unavailable)</span>}
          </li>
        );
      })}
    </ul>
  );
}

function Diagnosis({ diagnosis, evidence, status }) {
  return (
    <section aria-labelledby="ai-heading" className={`${workspacePanel} space-y-4 p-4 sm:p-5`}>
      <h2 id="ai-heading" className="text-lg font-semibold">AI diagnosis</h2>
      {status === "completed" && diagnosis ? (
        <>
          <p className="text-sm text-muted">AI-generated hypothesis. Review the evidence before acting on the suggested cause.</p>
          <p className="wrap-break-word whitespace-pre-wrap">{diagnosis.summary}</p>
          <div>
            <h3 className="text-sm font-semibold">Most likely root cause</h3>
            <p className="mt-1 text-sm wrap-break-word whitespace-pre-wrap text-muted">{diagnosis.root_cause}</p>
          </div>
          <p className="text-sm">Model confidence <strong className="font-medium text-accent">{diagnosis.confidence.replaceAll("_", " ")}</strong></p>
          {diagnosis.suggested_actions.length > 0 && (
            <div>
              <h3 className="text-sm font-semibold">Suggested next actions</h3>
              <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm wrap-break-word whitespace-pre-wrap text-muted">
                {diagnosis.suggested_actions.map((action, index) => <li key={`${index}:${action}`}>{action}</li>)}
              </ol>
            </div>
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

function Classification({ classification }) {
  return (
    <section aria-labelledby="classification-heading" className={`${workspacePanel} space-y-2 p-4 sm:p-5`}>
      <h2 id="classification-heading" className="text-lg font-semibold">Preliminary failure classification</h2>
      {classification ? (
        <>
          <p className="text-sm text-foreground">{categoryLabels[classification.category]} <span className="text-muted">· Heuristic score {Math.round(classification.confidence * 100)}%</span></p>
          <ul className="list-disc space-y-1 pl-5 text-sm text-muted">
            {classification.reasons.map((reason, index) => <li key={`${index}:${reason}`}>{reason}</li>)}
          </ul>
        </>
      ) : <p className="text-sm text-muted">A preliminary classification is unavailable.</p>}
    </section>
  );
}

export function Investigation({ repoId, runId, attempt }) {
  const request = useWorkflowData(`/${encodeURIComponent(repoId)}/runs/${encodeURIComponent(runId)}/attempts/${encodeURIComponent(attempt)}`);
  const { data } = request;
  const { repository, run, diagnosis, evidence } = data || {};
  const workflowUrl = githubRunUrl(run?.html_url);

  return (
    <>
      <Link href={repositoryPath(repoId)} className="text-sm text-accent underline-offset-4 hover:underline">Back to workflow runs</Link>
      <RequestStatus {...request} />
      {data && (
        <div className="mt-4 space-y-5">
          <header className="space-y-3 border-b border-line pb-6">
            <p className="font-mono text-sm wrap-break-word text-muted">{repository.owner}/{repository.name}</p>
            <h1 className="text-3xl font-semibold tracking-tight wrap-break-word">{workflowName(run)}</h1>
            <p className="font-mono text-xs wrap-break-word text-muted">
              {run.run_number != null && `#${run.run_number} · `}Run {run.run_id} · Attempt {run.run_attempt} · {run.branch || "Branch unavailable"}
            </p>
            <p className="text-sm text-muted">Commit <code className="wrap-break-word text-foreground">{run.commit_sha}</code></p>
            {workflowUrl && (
              <a href={workflowUrl} className="inline-flex items-center gap-1 text-sm text-accent underline-offset-4 hover:underline">
                View GitHub Actions run <ArrowUpRight size={15} aria-hidden="true" />
              </a>
            )}
            <RunStatuses run={run} />
            <p role="status" className={`text-sm ${run.collection_status === "failed" ? "text-failure" : "text-muted"}`}>
              {collectionMessages[run.collection_status] || "Evidence collection status is unavailable."}
            </p>
          </header>
          <Diagnosis diagnosis={diagnosis} evidence={evidence} status={run.investigation_status} />
          <Classification classification={run.classification} />
          <EvidenceExplorer evidence={evidence} />
        </div>
      )}
    </>
  );
}
