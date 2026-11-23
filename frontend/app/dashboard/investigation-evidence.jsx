export function evidenceAnchor(reference) {
  return `evidence-${encodeURIComponent(reference)}`;
}

function EvidenceItem({ title, id, children }) {
  return (
    <details id={id} className="scroll-mt-4 border-t border-line open:bg-raised/40 target:bg-accent-soft">
      <summary className="cursor-pointer px-4 py-3 text-sm font-medium wrap-break-word hover:text-accent sm:px-5">{title}</summary>
      <div className="space-y-3 px-4 pb-4 text-sm text-muted sm:px-5">{children}</div>
    </details>
  );
}

function EvidenceText({ children }) {
  return <pre tabIndex={0} className="max-h-96 overflow-auto rounded-sm border border-line bg-background p-3 font-mono text-xs leading-relaxed whitespace-pre text-foreground">{children}</pre>;
}

export function EvidenceExplorer({ evidence }) {
  const { failed_jobs, failure_packets, changed_files, correlations, base_sha, diff_available, diff_may_be_incomplete } = evidence;
  const jobs = new Map(failed_jobs.map((job) => [job.job_id, { job }]));
  for (const packet of failure_packets) jobs.set(packet.job_id, { ...jobs.get(packet.job_id), packet });

  return (
    <section aria-labelledby="evidence-heading" className="rounded-md border border-line bg-panel">
      <div className="space-y-2 px-4 py-4 sm:px-5">
        <h2 id="evidence-heading" className="text-lg font-semibold">Evidence explorer</h2>
        {base_sha && <p className="text-sm text-muted">Last passing commit <code className="wrap-break-word text-foreground">{base_sha}</code></p>}
        {diff_may_be_incomplete && <p className="text-sm text-muted">The commit comparison may be incomplete.</p>}
      </div>
      <h3 className="border-t border-line px-4 py-3 text-sm font-semibold sm:px-5">Failed jobs and logs</h3>
      {!jobs.size && <p className="border-t border-line px-4 py-3 text-sm text-muted sm:px-5">No failed job evidence is available.</p>}
      {[...jobs.entries()].map(([id, { job, packet }]) => (
        <EvidenceItem key={id} id={evidenceAnchor(`log:${id}`)} title={`${job?.name || packet.job_name} · Job ${id}`}>
          {job?.failed_steps.length > 0 ? (
            <ul className="list-disc space-y-1 pl-5">
              {job.failed_steps.map((step) => <li key={step.number}>Step {step.number} · {step.name}</li>)}
            </ul>
          ) : packet?.failed_step && <p>Failed step · {packet.failed_step}</p>}
          {packet?.error_type && <p className="font-mono text-failure">{packet.error_type}</p>}
          {packet?.error_message && <p className="font-mono wrap-break-word whitespace-pre-wrap">{packet.error_message}</p>}
          {packet?.failing_tests.length > 0 && (
            <div>
              <p className="font-medium text-foreground">Failing tests</p>
              <ul className="mt-1 list-disc space-y-1 pl-5 font-mono text-xs wrap-break-word">
                {packet.failing_tests.map((name, index) => <li key={`${index}:${name}`}>{name}</li>)}
              </ul>
            </div>
          )}
          {packet?.relevant_log ? <EvidenceText>{packet.relevant_log}</EvidenceText> : <p>No reduced log excerpt is available.</p>}
        </EvidenceItem>
      ))}
      <h3 className="border-t border-line px-4 py-3 text-sm font-semibold sm:px-5">Changed files and patches</h3>
      {!changed_files.length && (
        <p className="border-t border-line px-4 py-3 text-sm text-muted sm:px-5">
          {diff_available ? "No changed files were retained for this comparison." : "A commit comparison is unavailable."}
        </p>
      )}
      {changed_files.map((file) => (
        <EvidenceItem key={file.filename} id={evidenceAnchor(`diff:${file.filename}`)} title={file.filename}>
          <p className="font-mono text-xs">{file.status} · +{file.additions} / −{file.deletions}</p>
          {file.previous_filename && <p className="wrap-break-word">Previous path · {file.previous_filename}</p>}
          {file.truncated && <p>This patch was truncated during collection.</p>}
          {file.patch ? <EvidenceText>{file.patch}</EvidenceText> : <p>No patch text is available for this file.</p>}
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
