"use client";

import Link from "next/link";
import { ArrowRight, CircleAlert, GitFork } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { repositoryPath, useWorkflowData } from "./workflow-data";
import { RequestStatus } from "./workflow-ui";

export function Repositories({ installationId, cache }) {
  const [offsets, setOffsets] = useState([0]);
  const request = useWorkflowData(`?installation_id=${installationId}&offset=${offsets.at(-1)}`, cache);
  const { data } = request;

  return (
    <div className="border-t border-line px-4 pb-4 sm:px-5 sm:pb-5">
      <RequestStatus {...request} />
      {data?.repositories.length === 0 && (
        <p className="pt-4 text-sm text-muted">No repositories with verified access. Link this installation again to verify repository access.</p>
      )}
      {data?.repositories.length > 0 && (
        <ul aria-label="Repositories" className="mt-4 grid grid-cols-[repeat(auto-fill,minmax(min(100%,15rem),1fr))] gap-3">
          {data.repositories.map((repo) => {
            const hasFailures = repo.recent_runs.length > 0;
            const Icon = hasFailures ? CircleAlert : GitFork;
            return (
              <li key={repo.id} className="min-w-0">
                <Link
                  href={repositoryPath(repo.id)}
                  className={`group flex h-full min-h-24 items-start gap-3 rounded-sm border p-4 ${hasFailures
                    ? "border-failure/60 bg-[#351d1b] hover:border-failure hover:bg-[#412320]"
                    : "border-line bg-raised hover:border-accent/60"}`}
                >
                  <Icon size={17} className={`mt-0.5 shrink-0 ${hasFailures ? "text-failure" : "text-muted"}`} aria-hidden="true" />
                  <span className="min-w-0 flex-1">
                    <span className="block text-xs wrap-break-word text-muted">{repo.owner}</span>
                    <span className="block text-sm font-semibold wrap-break-word">{repo.name}</span>
                    <span className={`mt-2 block text-xs ${hasFailures ? "text-failure" : "text-muted"}`}>
                      {hasFailures ? "Failed runs recorded" : "No failed runs recorded"}
                    </span>
                  </span>
                  <ArrowRight size={16} className="mt-0.5 shrink-0 text-muted group-hover:text-foreground" aria-hidden="true" />
                </Link>
              </li>
            );
          })}
        </ul>
      )}
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
    </div>
  );
}
