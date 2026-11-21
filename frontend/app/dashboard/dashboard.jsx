"use client";

import { RedirectToSignIn, SignOutButton, UserAvatar, useAuth, useUser } from "@clerk/nextjs";
import { LogOut } from "lucide-react";
import { useEffect, useState } from "react";
import { apiFetch } from "../../lib/api";
import { AuthBrand } from "../components/auth/auth-branding";
import { GithubIntegration } from "./github-integration";
import "../components/auth/auth.css";

const retryDelays = [1000, 2000, 4000];
const actionClass = "inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-[#37373d] bg-[#18181c] px-4 text-sm font-medium transition-colors hover:bg-[#24242b] motion-reduce:transition-none";

export function DashboardLoading() {
  return (
    <main className="grid min-h-dvh place-items-center bg-[#09090b] text-[#a1a1aa]">
      <p role="status" className="text-sm">Loading your workspace…</p>
    </main>
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
    <main className="auth-shell min-h-dvh bg-[#09090b] text-[#fafafa]">
      <header className="border-b border-[#27272a]">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-4 px-5 py-4 sm:px-8">
          <AuthBrand />
          <SignOutButton redirectUrl="/sign-in">
            <button type="button" className={actionClass}><LogOut size={16} aria-hidden="true" /> Sign out</button>
          </SignOutButton>
        </div>
      </header>
      <div className="mx-auto max-w-5xl px-5 py-12 sm:px-8 sm:py-20">
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
          {name ? `Welcome, ${name}` : "Welcome to CI Postmortem Agent"}
        </h1>
        <p className="mt-2 text-sm leading-6 text-[#a1a1aa]">Investigate failed GitHub Actions workflows from your workspace.</p>

        <section aria-labelledby="profile-heading" className="mt-8 rounded-xl border border-[#27272a] bg-[#111114] p-5 sm:p-6">
          <h2 id="profile-heading" className="text-sm font-semibold">Your account</h2>
          <div className="mt-4 flex items-center gap-4">
            <UserAvatar appearance={{ elements: { avatarBox: { width: "3rem", height: "3rem" } } }} />
            <div className="min-w-0">
              {name && <p className="break-words text-sm font-medium">{name}</p>}
              {email && <p className="break-words text-sm text-[#a1a1aa]">{email}</p>}
            </div>
          </div>
          <p role="status" className={profileMessage ? "mt-4 text-sm text-[#a1a1aa]" : "sr-only"}>
            {profileMessage || "Workspace profile loaded."}
          </p>
          {(status === "pending" || status === "error") && (
            <button
              type="button"
              className={`mt-3 ${actionClass}`}
              onClick={() => {
                setStatus("loading");
                setRequest((previous) => previous + 1);
              }}
            >
              Retry profile sync
            </button>
          )}
        </section>

        <GithubIntegration getToken={getToken} actionClass={actionClass} />
      </div>
    </main>
  );
}
