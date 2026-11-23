"use client";

import Link from "next/link";
import { useState } from "react";
import { useWorkflowData } from "../../workflow-data";
import { categoryLabels, investigationLabels, RequestStatus, RunList, workspacePanel } from "../../workflow-ui";

const selectClass = "min-h-10 rounded-sm border border-line bg-panel px-3 text-sm text-foreground";

export function RepositoryRuns({ repoId }) {
  const request = useWorkflowData(`/${encodeURIComponent(repoId)}/runs`);
  const [status, setStatus] = useState("");
  const [category, setCategory] = useState("");
  const { data } = request;
  const runs = data?.runs.filter((run) => (!status || run.investigation_status === status)
    && (!category || run.classification?.category === category));

  return (
    <>
      <Link href="/dashboard#repositories" className="text-sm text-accent underline-offset-4 hover:underline">All repositories</Link>
      <header className="mt-4 border-b border-line pb-6">
        {data && <p className="mb-2 font-mono text-sm wrap-break-word text-muted">{data.repository.owner}/{data.repository.name}</p>}
        <h1 className="text-3xl font-semibold tracking-tight">Workflow runs</h1>
        <p className="mt-2 text-sm text-muted">Latest 50 recorded GitHub Actions failures, including rerun attempts.</p>
      </header>
      <RequestStatus {...request} />
      {data && (
        <>
          <div className="my-5 flex flex-wrap gap-4">
            <label className="grid gap-1.5 text-xs font-medium text-muted">
              Investigation status
              <select className={selectClass} value={status} onChange={(event) => setStatus(event.target.value)}>
                <option value="">All statuses</option>
                {Object.entries(investigationLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
            <label className="grid gap-1.5 text-xs font-medium text-muted">
              Preliminary category
              <select className={selectClass} value={category} onChange={(event) => setCategory(event.target.value)}>
                <option value="">All categories</option>
                {Object.entries(categoryLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
          </div>
          <div className={workspacePanel}>
            {runs.length ? <RunList repoId={repoId} runs={runs} showClassification />
              : <p className="p-5 text-sm text-muted">{data.runs.length ? "No recent runs match these filters." : "No failed workflow runs recorded."}</p>}
          </div>
        </>
      )}
    </>
  );
}
