"use client";

import { useAuth } from "@clerk/nextjs";
import { ArrowUpRight } from "lucide-react";
import Link from "next/link";
import { Diagnosis } from "./investigation-diagnosis";
import { SuggestedFix } from "./suggested-fix";
import { repositoryPath, useWorkflowData } from "./workflow-data";
import { RequestStatus, RunStatuses, workflowName } from "./workflow-ui";

const collectionMessages = {
  pending: "Evidence collection is queued.",
  collecting: "Collecting failed jobs, reduced logs, and commit evidence.",
  completed: "Evidence collection is complete. Some sources may have been unavailable.",
  failed: "We couldn’t finish collecting evidence. Check the workflow in GitHub Actions for the original logs.",
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

export function Investigation({ repoId, runId, attempt }) {
  const { getToken, sessionId } = useAuth();
  const runPath = `/${encodeURIComponent(repoId)}/runs/${encodeURIComponent(runId)}/attempts/${encodeURIComponent(attempt)}`;
  const request = useWorkflowData(runPath);
  const { data } = request;
  const { repository, run, diagnosis, evidence, fix_suggestion, pr_comment } = data || {};
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
          <Diagnosis key={`${sessionId}:${runPath}`} diagnosis={diagnosis} evidence={evidence} status={run.investigation_status} runPath={runPath} getToken={getToken} />
          {(fix_suggestion || pr_comment) && <SuggestedFix fix={fix_suggestion} publication={pr_comment} />}
        </div>
      )}
    </>
  );
}
