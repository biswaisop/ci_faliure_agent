"use client";

import { ArrowUpRight, Building2, ChevronDown, GitFork, GitPullRequest, UserRound } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { authorizeGithub, githubRequest } from "./github/github-api";
import { Repositories } from "./repositories";
import { workspacePanel } from "./workflow-ui";

export function GithubIntegration({ getToken }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [authorizing, setAuthorizing] = useState(false);
  const [request, setRequest] = useState(0);
  const [openInstallation, setOpenInstallation] = useState(null);
  const [repositoryCache] = useState(() => new Map());

  useEffect(() => {
    const controller = new AbortController();
    githubRequest(getToken, "", undefined, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setData(result);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setError(cause.message);
      });
    return () => controller.abort();
  }, [getToken, request]);

  async function linkExisting() {
    setAuthorizing(true);
    setError("");
    try {
      await authorizeGithub(getToken, {});
    } catch (cause) {
      setError(cause.message);
      setAuthorizing(false);
    }
  }

  return (
    <section id="installations" aria-labelledby="github-heading" className="mt-7 scroll-mt-6 sm:mt-9">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 id="github-heading" className="text-lg font-semibold tracking-tight">GitHub installations</h2>
          <p className="mt-1 text-sm text-muted">Open a GitHub account or organization to view its repositories.</p>
        </div>
        {data?.install_url && (
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" disabled={authorizing} onClick={linkExisting}>
              {authorizing ? "Opening GitHub…" : "Link existing installation"}
            </Button>
            <Button asChild>
              <a href={data.install_url}>
                <GitFork size={16} aria-hidden="true" />
                {data.installations.length ? "Install on another account" : "Connect GitHub"}
                <ArrowUpRight size={15} aria-hidden="true" />
              </a>
            </Button>
          </div>
        )}
      </div>

      <div className="space-y-3">
        {error && (
          <div className={`${workspacePanel} px-4 py-5 sm:px-5`}>
            <p role="alert" className="text-sm text-failure">{error}</p>
            {!data && (
              <Button
                type="button"
                variant="outline"
                className="mt-3"
                onClick={() => {
                  setError("");
                  setRequest((previous) => previous + 1);
                }}
              >
                Retry connection check
              </Button>
            )}
          </div>
        )}
        {!data && !error && <p role="status" className={`${workspacePanel} px-4 py-5 text-sm text-muted sm:px-5`}>Checking GitHub connections…</p>}
        {data?.installations.map((installation) => {
          const { id, account, status, target_type } = installation;
          const organization = target_type === "Organization";
          const AccountIcon = organization ? Building2 : UserRound;
          const suspended = status === "suspended";
          const open = openInstallation === id;
          const panelId = `installation-${id}-repositories`;

          return (
            <div key={id} className={workspacePanel}>
              <h3>
                <button
                  type="button"
                  aria-expanded={open}
                  aria-controls={panelId}
                  onClick={() => setOpenInstallation(open ? null : id)}
                  className="flex w-full cursor-pointer items-center gap-3 rounded-md px-4 py-4 text-left hover:bg-raised sm:px-5"
                >
                  <span className="grid size-10 shrink-0 place-items-center rounded-sm bg-accent-soft text-accent">
                    <AccountIcon size={19} aria-hidden="true" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block font-semibold wrap-break-word">{account}</span>
                    <span className="block text-xs text-muted">{organization ? "Organization" : "Personal"}</span>
                  </span>
                  <span className={`rounded-full px-2 py-1 text-xs font-medium ${suspended ? "bg-failure/10 text-failure" : "bg-accent-soft text-accent"}`}>
                    {suspended ? "Suspended" : "Connected"}
                  </span>
                  <ChevronDown size={17} className={`shrink-0 text-muted ${open ? "rotate-180" : ""}`} aria-hidden="true" />
                </button>
              </h3>
              <div id={panelId} hidden={!open}>
                {open && (suspended ? (
                  <p className="border-t border-line px-4 py-4 text-sm text-muted sm:px-5">Repositories are unavailable while this installation is suspended.</p>
                ) : <Repositories installationId={id} cache={repositoryCache} />)}
              </div>
            </div>
          );
        })}
        {data?.installations.length === 0 && (
          <div className={`${workspacePanel} px-4 py-10 text-center text-sm sm:py-14`}>
            <span className="mx-auto grid size-11 place-items-center rounded-md border border-line bg-accent-soft text-accent">
              <GitPullRequest size={21} aria-hidden="true" />
            </span>
            <p className="mt-4 font-semibold">No GitHub installations yet</p>
            {data.install_url && (
              <p className="mx-auto mt-1 max-w-xs leading-6 text-muted">Install the GitHub App or link an existing installation to get started.</p>
            )}
          </div>
        )}
        {data && !data.install_url && (
          <p className={`${workspacePanel} px-4 py-4 text-sm text-muted sm:px-5`}>GitHub App setup is incomplete. Contact your workspace administrator.</p>
        )}
      </div>
    </section>
  );
}
