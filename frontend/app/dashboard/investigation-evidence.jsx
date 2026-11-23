import { useState } from "react";
import { EvidenceCode } from "./evidence-code";
import { comparisonLabel, evidenceAnchor } from "./evidence-format";

function EvidenceItem({ title, id, children }) {
  const [open, setOpen] = useState(false);
  return (
    <details id={id} onToggle={(event) => setOpen(event.currentTarget.open)} className="scroll-mt-4 border-t border-line open:bg-raised/40 target:bg-accent-soft">
      <summary className="cursor-pointer px-4 py-3 text-sm font-medium wrap-break-word hover:text-accent sm:px-5">{title}</summary>
      {open && <div className="min-w-0 space-y-3 px-4 pb-4 text-sm text-muted sm:px-5">{children}</div>}
    </details>
  );
}

function EvidenceCompleteness({ evidence, jobs }) {
  const { base_sha, comparison_status, diff_available, changed_files, files_limit_reached, correlations } = evidence;
  const logs = [...jobs.values()].filter(({ job, packet }) => job?.log_status === "available"
    || (packet && packet.extraction_status !== "log_unavailable")).length;
  const patchCount = changed_files.filter((file) => file.patch && !file.truncated).length;
  const verified = comparison_status === "available" || comparison_status === "partial";
  const items = [
    ["Failed-job logs", jobs.size ? `${logs} of ${jobs.size} collected` : "No jobs recorded"],
    ["Passing baseline", verified ? "Verified ancestor" : comparison_status === "same_commit" ? "Same commit" : base_sha ? "Candidate only" : "Unavailable"],
    ["Commit comparison", verified ? (comparison_status === "partial" ? "Partial" : "Available") : diff_available ? "Previously collected" : "Unavailable"],
    ["Patch text", changed_files.length ? `${patchCount} of ${changed_files.length} files with untrimmed text${files_limit_reached ? " · file limit reached" : ""}` : "No patch text"],
    ["File correlations", correlations.length ? `${correlations.length} recorded` : "None recorded"],
  ];

  return (
    <div className="space-y-2 rounded-sm border border-line bg-background px-3 py-3">
      <h3 className="text-sm font-semibold">Evidence completeness</h3>
      <dl className="grid gap-x-5 gap-y-2 text-xs sm:grid-cols-2 lg:grid-cols-3">
        {items.map(([label, value]) => (
          <div key={label}>
            <dt className="text-muted">{label}</dt>
            <dd className="mt-0.5 text-foreground">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export function EvidenceExplorer({ evidence }) {
  const { failed_jobs, failure_packets, changed_files, correlations, base_sha, diff_available, comparison_status } = evidence;
  const jobs = new Map(failed_jobs.map((job) => [job.job_id, { job }]));
  for (const packet of failure_packets) jobs.set(packet.job_id, { ...jobs.get(packet.job_id), packet });

  return (
    <section aria-labelledby="evidence-heading" className="rounded-md border border-line bg-panel">
      <div className="space-y-2 px-4 py-4 sm:px-5">
        <h2 id="evidence-heading" className="text-lg font-semibold">Evidence explorer</h2>
        {base_sha && <p className="text-sm text-muted">
          {comparison_status === "baseline_not_ancestor" || comparison_status === "comparison_failed" ? "Candidate passing commit" : "Earlier passing commit"}{" "}
          <code className="wrap-break-word text-foreground">{base_sha}</code>
        </p>}
        <EvidenceCompleteness evidence={evidence} jobs={jobs} />
      </div>
      <h3 className="border-t border-line px-4 py-3 text-sm font-semibold sm:px-5">Failed jobs and logs</h3>
      {!jobs.size && <p className="border-t border-line px-4 py-3 text-sm text-muted sm:px-5">No failed job evidence is available.</p>}
      {[...jobs.entries()].map(([id, { job, packet }]) => (
        <EvidenceItem key={id} id={evidenceAnchor(`log:${id}`)} title={`${job?.name || packet.job_name} · Job ${id}`}>
          {job?.failed_steps?.length > 0 ? (
            <ul className="list-disc space-y-1 pl-5">
              {job.failed_steps.map((step) => <li key={step.number}>Step {step.number} · {step.name}</li>)}
            </ul>
          ) : packet?.failed_step && <p>Failed step · {packet.failed_step}</p>}
          {packet?.error_type && <p className="font-mono text-failure">{packet.error_type}</p>}
          {packet?.error_message && <p className="font-mono wrap-break-word whitespace-pre-wrap">{packet.error_message}</p>}
          {packet?.failing_tests?.length > 0 && (
            <div>
              <p className="font-medium text-foreground">Failing tests</p>
              <ul className="mt-1 list-disc space-y-1 pl-5 font-mono text-xs wrap-break-word">
                {packet.failing_tests.map((name, index) => <li key={`${index}:${name}`}>{name}</li>)}
              </ul>
            </div>
          )}
          {packet?.relevant_log ? <EvidenceCode kind="log" text={packet.relevant_log} /> : <p>No reduced log excerpt is available.</p>}
        </EvidenceItem>
      ))}
      <h3 className="border-t border-line px-4 py-3 text-sm font-semibold sm:px-5">Changed files and patches</h3>
      <p className="border-t border-line px-4 py-3 text-sm text-muted sm:px-5">
        {comparisonLabel(evidence)} {diff_available && !changed_files.length && "GitHub returned no changed files."}
      </p>
      {changed_files.map((file) => (
        <EvidenceItem key={file.filename} id={evidenceAnchor(`diff:${file.filename}`)} title={`${file.filename} · ${file.status}`}>
          <p className="font-mono text-xs">{file.status} · +{file.additions} / −{file.deletions}</p>
          {file.previous_filename && <p className="wrap-break-word">Previous path · {file.previous_filename}</p>}
          {(file.patch_status === "truncated" || (file.patch && file.truncated)) && <p>Patch text exceeds the evidence collection limit.</p>}
          {file.patch ? <EvidenceCode kind="diff" text={file.patch} />
            : file.patch_status !== "truncated" && <p>GitHub supplied file metadata without patch text.</p>}
        </EvidenceItem>
      ))}
      <EvidenceItem title={`File correlations (${correlations.length})`}>
        {correlations.length ? (
          <ul className="space-y-2 font-mono text-xs wrap-break-word">
            {correlations.map((item, index) => (
              <li key={`${index}:${item.changed_file}`}>
                {item.failing_file} → {item.changed_file} <span>({item.match_type.replaceAll("_", " ")})</span>
              </li>
            ))}
          </ul>
        ) : <p>No file correlations were recorded.</p>}
      </EvidenceItem>
    </section>
  );
}
