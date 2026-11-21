"use client";

import { ArrowUpRight, Building2, GitPullRequest, UserRound } from "lucide-react";
import { useEffect, useState } from "react";
import { authorizeGithub, githubRequest } from "./github/github-api";

export function GithubIntegration({ getToken, actionClass }) {
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
    <section aria-labelledby="github-heading" className="mt-4 rounded-xl border border-[#27272a] bg-[#111114] p-5 sm:p-6">
      <h2 id="github-heading" className="flex items-center gap-2 text-sm font-semibold">
        <GitPullRequest size={18} className="text-[#a5a7fc]" aria-hidden="true" /> GitHub integration
      </h2>
      <p className="mt-2 max-w-xl text-sm leading-6 text-[#a1a1aa]">
        Install the GitHub App on your personal account or an organization. Choose which repositories it can access on GitHub.
      </p>

      {!data && !error && <p role="status" className="mt-4 text-sm text-[#a1a1aa]">Checking GitHub connections…</p>}
      {error && (
        <div className="mt-4">
          <p role="alert" className="text-sm text-[#fca5a5]">{error}</p>
          {!data && (
            <button
              type="button"
              className={`mt-3 ${actionClass}`}
              onClick={() => {
                setError("");
                setRequest((previous) => previous + 1);
              }}
            >
              Retry connection check
            </button>
          )}
        </div>
      )}
      {data?.installations.length > 0 && (
        <ul className="mt-4 divide-y divide-[#27272a] rounded-lg border border-[#27272a]">
          {data.installations.map((installation) => {
            const organization = installation.target_type === "Organization";
            const AccountIcon = organization ? Building2 : UserRound;
            return (
              <li key={installation.id} className="flex flex-wrap items-center justify-between gap-2 px-3.5 py-3">
                <span className="flex min-w-0 items-center gap-3 text-sm">
                  <AccountIcon size={17} className="shrink-0 text-[#a5a7fc]" aria-hidden="true" />
                  <span className="break-all font-medium">{installation.account}</span>
                  <span className="text-xs text-[#a1a1aa]">{organization ? "Organization" : "Personal"}</span>
                </span>
                <span className={installation.status === "suspended" ? "text-xs text-[#fca5a5]" : "text-xs text-[#86efac]"}>
                  {installation.status === "suspended" ? "Suspended" : "Connected"}
                </span>
              </li>
            );
          })}
        </ul>
      )}
      {data?.install_url && (
        <div className="mt-4 flex flex-wrap gap-2">
          <a href={data.install_url} className={actionClass}>
            {data.installations.length ? "Install on another account" : "Connect GitHub"}
            <ArrowUpRight size={15} aria-hidden="true" />
          </a>
          <button
            type="button"
            disabled={authorizing}
            onClick={linkExisting}
            className={`${actionClass} disabled:opacity-60`}
          >
            {authorizing ? "Opening GitHub…" : "Link existing installation"}
          </button>
        </div>
      )}
      {data && !data.install_url && (
        <p className="mt-4 text-sm text-[#a1a1aa]">GitHub App setup is incomplete. Contact your workspace administrator.</p>
      )}
    </section>
  );
}
