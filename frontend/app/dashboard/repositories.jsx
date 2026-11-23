"use client";

import Link from "next/link";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { repositoryPath, useWorkflowData } from "./workflow-data";
import { RequestStatus, RunList, StatusBadge, workspacePanel } from "./workflow-ui";

export function Repositories() {
  const [offsets, setOffsets] = useState([0]);
  const request = useWorkflowData(`?offset=${offsets.at(-1)}`);
  const { data } = request;

  return (
    <section id="repositories" aria-labelledby="repositories-heading" className="mt-7 scroll-mt-6 sm:mt-9">
      <h2 id="repositories-heading" className="text-lg font-semibold tracking-tight">Repositories</h2>
      <p className="mt-1 text-sm text-muted">Recent GitHub Actions failures across your connected repositories.</p>
      <RequestStatus {...request} />
      {data?.repositories.length === 0 && (
        <div className={`${workspacePanel} mt-4 p-5 text-sm text-muted`}>
          <p>No repositories with verified access are available yet.</p>
          <p className="mt-2">Connect GitHub, or link an existing installation again to verify repository access.</p>
          <a href="#installations" className="mt-3 inline-block text-accent underline-offset-4 hover:underline">Manage GitHub installations</a>
        </div>
      )}
      <div className="mt-4 space-y-4">
        {data?.repositories.map((repo) => (
          <section key={repo.id} className={workspacePanel} aria-labelledby={`repo-${repo.id}`}>
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3 sm:px-5">
              <h3 id={`repo-${repo.id}`} className="min-w-0 font-semibold wrap-break-word">
                <Link href={repositoryPath(repo.id)} className="underline-offset-4 hover:text-accent hover:underline">
                  <span className="text-muted">{repo.owner}/</span>{repo.name}
                </Link>
              </h3>
              <StatusBadge tone="completed">GitHub connected</StatusBadge>
            </div>
            {repo.recent_runs.length ? <RunList repoId={repo.id} runs={repo.recent_runs} />
              : <p className="px-4 py-4 text-sm text-muted sm:px-5">No failed workflow runs recorded.</p>}
          </section>
        ))}
      </div>
      {(offsets.length > 1 || data?.next_offset != null) && (
        <nav aria-label="Repository pages" className="mt-4 flex flex-wrap gap-2">
          <Button variant="outline" type="button" disabled={offsets.length === 1} onClick={() => setOffsets(offsets.slice(0, -1))}>
            Previous repositories
          </Button>
          <Button variant="outline" type="button" disabled={data?.next_offset == null} onClick={() => setOffsets([...offsets, data.next_offset])}>
            Next repositories
          </Button>
        </nav>
      )}
    </section>
  );
}
