"use client";

import { RedirectToSignIn, useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { authorizeGithub, githubRequest } from "./github-api";

const buttonClass = "mt-4 inline-flex min-h-11 items-center rounded-lg bg-[#5455dc] px-4 text-sm font-semibold text-white transition-colors hover:bg-[#494ac6] disabled:opacity-60";

export function GithubCallback() {
  const params = useSearchParams();
  const router = useRouter();
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const submitted = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  const code = params.get("code");
  const state = params.get("state");
  const installationId = Number(params.get("installation_id"));
  const requested = params.get("setup_action") === "request";
  const denied = params.has("error");
  const returning = Boolean(code && state && !denied);
  const needsVerification = !code && !requested && !denied
    && Number.isSafeInteger(installationId) && installationId > 0;

  useEffect(() => {
    if (!isSignedIn || !returning || submitted.current) return;
    submitted.current = true;
    githubRequest(getToken, "/complete", { code, state })
      .then((data) => {
        if (data.status === "connected") router.replace("/dashboard");
        else setResult(data);
      })
      .catch((cause) => setError(cause.message));
  }, [code, getToken, isSignedIn, returning, router, state]);

  if (isLoaded && !isSignedIn) return <RedirectToSignIn />;

  async function verify() {
    setBusy(true);
    setError("");
    try {
      await authorizeGithub(getToken, { installation_id: installationId });
    } catch (cause) {
      setError(cause.message);
      setBusy(false);
    }
  }

  async function link(installationId) {
    setBusy(true);
    setError("");
    try {
      await githubRequest(getToken, "/link", { state: result.state, installation_id: installationId });
      router.replace("/dashboard");
    } catch (cause) {
      setError(cause.message);
      setBusy(false);
    }
  }

  let message = "Return to the dashboard to start GitHub setup.";
  if (!isLoaded) message = "Loading your session…";
  else if (denied) message = "GitHub authorization was canceled.";
  else if (requested) message = "An organization owner must approve your request. Once approved, return to the dashboard and choose Link existing installation.";
  else if (result?.status === "select") message = "Choose which GitHub installation to link to your workspace.";
  else if (result?.status === "empty") message = "GitHub did not return any accessible installations. Install the app on GitHub or wait for organization approval.";
  else if (returning) message = error ? "Return to the dashboard to restart GitHub authorization." : "Verifying your GitHub installation…";
  else if (needsVerification) message = "Authorize the app on GitHub so we can verify your access to this installation.";

  return (
    <>
      <p role="status" className="mt-3 text-sm leading-6 text-[#a1a1aa]">{message}</p>
      {error && <p role="alert" className="mt-3 text-sm text-[#fca5a5]">{error}</p>}
      {isLoaded && needsVerification && (
        <button type="button" disabled={busy} className={buttonClass} onClick={verify}>
          {busy ? "Opening GitHub…" : "Verify installation"}
        </button>
      )}
      {result?.status === "select" && (
        <ul className="mt-4 space-y-2">
          {result.installations.map((installation) => (
            <li key={installation.id}>
              <button
                type="button"
                disabled={busy}
                className="flex min-h-11 w-full items-center justify-between gap-2 rounded-lg border border-[#37373d] px-3 text-left text-sm hover:bg-[#22222a] disabled:opacity-60"
                onClick={() => link(installation.id)}
              >
                <span className="break-all">{installation.account}</span>
                <span className="shrink-0 text-xs text-[#a1a1aa]">
                  {installation.target_type === "Organization" ? "Organization" : "Personal"}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <Link href="/dashboard" prefetch={false} className="mt-5 block w-fit text-sm text-[#b0b2ff] underline-offset-4 hover:underline">
        Back to dashboard
      </Link>
    </>
  );
}
