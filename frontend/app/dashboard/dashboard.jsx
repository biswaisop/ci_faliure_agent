"use client";

import { RedirectToSignIn, SignOutButton, UserAvatar, useAuth, useUser } from "@clerk/nextjs";
import { LogOut } from "lucide-react";
import { useEffect, useState } from "react";
import { apiFetch } from "../../lib/api";
import { Button } from "@/components/ui/button";
import { GithubIntegration } from "./github-integration";
import { Repositories } from "./repositories";

const retryDelays = [1000, 2000, 4000];

export function DashboardLoading() {
  return (
    <p role="status" className="py-12 text-sm text-muted">Loading your workspace…</p>
  );
}

export function Dashboard() {
  const { isLoaded, isSignedIn, sessionId, getToken } = useAuth();
  const { user } = useUser();

  if (!isLoaded || (isSignedIn && !user)) return <DashboardLoading />;
  if (!isSignedIn) return <RedirectToSignIn />;

  return <Workspace key={sessionId} user={user} getToken={getToken} />;
}

function Workspace({ user, getToken }) {
  const [request, setRequest] = useState(0);
  const [status, setStatus] = useState("loading");
  const [profile, setProfile] = useState(null);

  useEffect(() => {
    const controller = new AbortController();
    let retryTimer;

    async function loadProfile(attempt = 0) {
      try {
        const response = await apiFetch(getToken, "/api/users/me", {
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(8000)]),
        });
        if (controller.signal.aborted) return;

        if (response.status === 404) {
          const delay = retryDelays[attempt];
          setStatus(delay ? "syncing" : "pending");
          if (delay) retryTimer = setTimeout(() => loadProfile(attempt + 1), delay);
          return;
        }

        if (!response.ok) throw new Error(`Profile request failed (${response.status}).`);
        const data = await response.json();
        if (controller.signal.aborted) return;

        setProfile(data);
        setStatus("ready");
      } catch {
        if (!controller.signal.aborted) setStatus("error");
      }
    }

    loadProfile();
    return () => {
      controller.abort();
      clearTimeout(retryTimer);
    };
  }, [getToken, request]);

  const name = profile?.name || user.fullName;
  const email = profile?.email || user.primaryEmailAddress?.emailAddress;
  const profileMessage = {
    loading: "Loading your workspace profile…",
    syncing: "Setting up your workspace profile. Checking again shortly…",
    pending: "Your workspace profile is still syncing. Try again in a moment.",
    error: "We couldn’t load your workspace profile. Try again to reconnect.",
  }[status];

  return (
    <>
      <header className="border-b border-line pb-6 sm:pb-8">
        <h1 className="text-3xl font-semibold tracking-[-0.045em] sm:text-4xl">Workspace overview</h1>
        <p className="mt-2 max-w-xl text-sm leading-6 text-muted">Connect GitHub repositories to investigate failed GitHub Actions workflows.</p>
      </header>
      <Repositories />
      <GithubIntegration getToken={getToken} />

      <section id="account" aria-labelledby="profile-heading" className="mt-10 scroll-mt-6 sm:mt-12">
        <h2 id="profile-heading" className="mb-4 text-lg font-semibold tracking-tight">Your account</h2>
        <div className="rounded-md border border-line bg-panel px-4 py-4 sm:px-5">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex min-w-0 items-center gap-3">
              <UserAvatar appearance={{ elements: { avatarBox: { width: "2.5rem", height: "2.5rem" } } }} />
              <div className="min-w-0 wrap-break-word">
                {name && <p className="text-sm font-medium">{name}</p>}
                {email && <p className="text-sm text-muted">{email}</p>}
              </div>
            </div>
            <SignOutButton redirectUrl="/sign-in">
              <Button type="button" variant="outline"><LogOut size={16} aria-hidden="true" /> Sign out</Button>
            </SignOutButton>
          </div>
          <p role="status" className={profileMessage ? "mt-4 text-sm text-muted" : "sr-only"}>
            {profileMessage || "Workspace profile loaded."}
          </p>
          {(status === "pending" || status === "error") && (
            <Button
              type="button"
              variant="outline"
              className="mt-3"
              onClick={() => {
                setStatus("loading");
                setRequest((previous) => previous + 1);
              }}
            >
              Retry profile sync
            </Button>
          )}
        </div>
      </section>
    </>
  );
}
