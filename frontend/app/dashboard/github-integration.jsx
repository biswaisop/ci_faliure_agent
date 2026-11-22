"use client";

import { ArrowUpRight, Building2, GitFork, GitPullRequest, UserRound } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { authorizeGithub, githubRequest } from "./github/github-api";

export function GithubIntegration({ getToken }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [authorizing, setAuthorizing] = useState(false);
  const [request, setRequest] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    githubRequest(getToken, "", undefined, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setData(result);
      })
      .catch(() => {
        if (!controller.signal.aborted) setError("Could not check GitHub connections. Try again.");
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
          <p className="mt-1 text-sm text-muted">Manage the GitHub accounts and organizations linked to AutopCI.</p>
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

      <div className="overflow-hidden rounded-md border border-line bg-panel">
        {error && (
          <div className="px-4 py-5 sm:px-5">
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
        {!data && !error && <p role="status" className="px-4 py-5 text-sm text-muted sm:px-5">Checking GitHub connections…</p>}
        {data?.installations.length > 0 && (
          <table className={`w-full table-fixed text-left text-sm ${error ? "border-t border-line" : ""}`}>
            <thead className="border-b border-line bg-raised text-xs text-muted">
              <tr>
                <th scope="col" className="px-4 py-3 font-medium sm:px-5">Account</th>
                <th scope="col" className="hidden w-32 px-4 py-3 font-medium sm:table-cell">Type</th>
                <th scope="col" className="w-32 px-4 py-3 text-right font-medium sm:w-36 sm:px-5">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {data.installations.map((installation) => {
                const organization = installation.target_type === "Organization";
                const AccountIcon = organization ? Building2 : UserRound;
                const type = organization ? "Organization" : "Personal";
                const suspended = installation.status === "suspended";
                return (
                  <tr key={installation.id}>
                    <th scope="row" className="px-4 py-4 font-medium sm:px-5">
                      <div className="flex items-center gap-3">
                        <AccountIcon size={17} className="shrink-0 text-accent" aria-hidden="true" />
                        <div className="min-w-0 wrap-break-word">
                          {installation.account}
                          <p className="mt-0.5 text-xs font-normal text-muted sm:hidden">{type}</p>
                        </div>
                      </div>
                    </th>
                    <td className="hidden px-4 py-4 text-muted sm:table-cell">{type}</td>
                    <td className="px-4 py-4 text-right sm:px-5">
                      <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-1 text-xs font-medium ${suspended ? "bg-failure/10 text-failure" : "bg-accent-soft text-accent"}`}>
                        <span className="size-1.5 rounded-full bg-current" aria-hidden="true" />
                        {suspended ? "Suspended" : "Connected"}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {data?.installations.length === 0 && (
          <div className="px-4 py-10 text-center text-sm sm:py-14">
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
          <p className="border-t border-line px-4 py-4 text-sm text-muted sm:px-5">GitHub App setup is incomplete. Contact your workspace administrator.</p>
        )}
      </div>
    </section>
  );
}
