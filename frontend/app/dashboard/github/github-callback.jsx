"use client";

import { RedirectToSignIn, useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { authorizeGithub, githubRequest } from "./github-api";

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
      <p role="status" className="mt-3 text-sm leading-6 text-muted">{message}</p>
      {error && <p role="alert" className="mt-3 text-sm text-failure">{error}</p>}
      {isLoaded && needsVerification && (
        <Button type="button" disabled={busy} className="mt-4" onClick={verify}>
          {busy ? "Opening GitHub…" : "Verify installation"}
        </Button>
      )}
      {result?.status === "select" && (
        <ul className="mt-4 space-y-2">
          {result.installations.map((installation) => (
            <li key={installation.id}>
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                className="w-full justify-between!"
                onClick={() => link(installation.id)}
              >
                <span className="min-w-0 wrap-break-word whitespace-normal text-left">{installation.account}</span>
                <span className="shrink-0 text-xs text-muted">
                  {installation.target_type === "Organization" ? "Organization" : "Personal"}
                </span>
              </Button>
            </li>
          ))}
        </ul>
      )}
      <Link href="/dashboard" prefetch={false} className="mt-5 block w-fit text-sm text-accent underline-offset-4 hover:underline">
        Back to dashboard
      </Link>
    </>
  );
}
