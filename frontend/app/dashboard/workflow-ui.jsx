import Link from "next/link";
import { Button } from "@/components/ui/button";
import { attemptPath } from "./workflow-data";

export const categoryLabels = {
  code_bug: "Code bug",
  dependency: "Dependency",
  infra_config: "Infrastructure / configuration",
  possible_flaky: "Possibly flaky",
  unknown: "Unknown",
};

export const investigationLabels = {
  pending: "Pending",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  unavailable: "Not available",
};

const collectionLabels = { pending: "Pending", collecting: "Collecting", completed: "Completed", failed: "Failed" };
export const workspacePanel = "rounded-md border border-line bg-panel";

export function workflowName(run) {
  return run.workflow_name || `Workflow ${run.workflow_id}`;
}

export function RequestStatus({ data, error, retry }) {
  if (error) return (
    <div className="my-4 flex flex-wrap items-center gap-3">
      <p role="alert" className="text-sm text-failure">{error}</p>
      <Button variant="outline" type="button" onClick={retry}>Retry</Button>
    </div>
  );
  if (!data) return <p role="status" className="py-6 text-sm text-muted">Loading workflow data…</p>;
  return null;
}

export function StatusBadge({ children, tone }) {
  const colors = tone === "completed" ? "bg-accent-soft text-accent"
    : tone === "failed" ? "bg-failure/10 text-failure" : "bg-raised text-muted";
  return <span className={`inline-flex rounded-sm px-2 py-1 text-xs font-medium ${colors}`}>{children}</span>;
}

export function RunStatuses({ run }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted">
      <span className="inline-flex items-center gap-1.5">
        Evidence <StatusBadge tone={run.collection_status}>{collectionLabels[run.collection_status] || "Not available"}</StatusBadge>
      </span>
      <span className="inline-flex items-center gap-1.5">
        Investigation <StatusBadge tone={run.investigation_status}>{investigationLabels[run.investigation_status] || "Not available"}</StatusBadge>
      </span>
    </div>
  );
}

const utcDate = new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" });

export function RunList({ repoId, runs, showClassification = false }) {
  return (
    <ul className="divide-y divide-line">
      {runs.map((run) => (
        <li key={`${run.run_id}:${run.run_attempt}`} className="grid gap-3 px-4 py-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:px-5">
          <div className="min-w-0 space-y-2">
            <Link href={attemptPath(repoId, run)} className="font-semibold wrap-break-word text-foreground underline-offset-4 hover:text-accent hover:underline">
              {workflowName(run)}
            </Link>
            <p className="font-mono text-xs wrap-break-word text-muted">
              {run.run_number != null && `#${run.run_number} · `}Run {run.run_id} · Attempt {run.run_attempt}
              {run.branch && ` · ${run.branch}`} · <code title={run.commit_sha}>{run.commit_sha.slice(0, 7)}</code>
            </p>
            <RunStatuses run={run} />
          </div>
          <div className="space-y-2 text-xs text-muted sm:text-right">
            <time dateTime={run.failed_at}>{utcDate.format(new Date(run.failed_at))} UTC</time>
            {showClassification && (
              <p><span className="text-failure capitalize">{run.conclusion}</span>
                {` · ${run.classification ? categoryLabels[run.classification.category] : "Classification unavailable"}`}
              </p>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
