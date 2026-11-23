"use client";

import { useAuth } from "@clerk/nextjs";
import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";

const apiPath = "/api/github/repositories";

export function repositoryPath(repoId) {
  return `/dashboard/repositories/${encodeURIComponent(repoId)}`;
}

export function attemptPath(repoId, run) {
  return `${repositoryPath(repoId)}/runs/${encodeURIComponent(run.run_id)}/attempts/${run.run_attempt}`;
}

function isProcessing(run) {
  return ["pending", "collecting"].includes(run.collection_status)
    || ["pending", "running"].includes(run.investigation_status);
}

function shouldPoll(data) {
  if (data.run) return isProcessing(data.run);
  if (data.runs) return data.runs.some(isProcessing);
  return data.repositories.some((repo) => repo.recent_runs.some(isProcessing));
}

export function useWorkflowData(path) {
  const { getToken, isSignedIn, sessionId } = useAuth();
  const [request, setRequest] = useState(0);
  const [result, setResult] = useState(null);
  const key = `${sessionId}:${path}:${request}`;

  useEffect(() => {
    if (!isSignedIn) return;
    const controller = new AbortController();
    let timer;
    let inFlight = false;
    let data = null;

    function schedule() {
      clearTimeout(timer);
      if (!controller.signal.aborted && !document.hidden && data && shouldPoll(data)) {
        timer = setTimeout(load, 5000);
      }
    }

    async function load() {
      if (inFlight) return;
      inFlight = true;
      try {
        const response = await apiFetch(getToken, `${apiPath}${path}`, {
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
        });
        if ([401, 403, 404, 422].includes(response.status)) {
          data = null;
          if (!controller.signal.aborted) setResult({ key, error: "This resource is unavailable for your session." });
          return;
        }
        if (!response.ok) throw new Error("Workflow request failed");
        data = await response.json();
        if (!controller.signal.aborted) setResult({ key, data });
      } catch {
        if (!controller.signal.aborted) setResult({ key, data, error: "Couldn’t load workflow data. Try again shortly." });
      } finally {
        inFlight = false;
        schedule();
      }
    }

    function syncVisibility() {
      clearTimeout(timer);
      if (!document.hidden && data && shouldPoll(data)) load();
    }

    document.addEventListener("visibilitychange", syncVisibility);
    load();
    return () => {
      controller.abort();
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", syncVisibility);
    };
  }, [getToken, isSignedIn, key, path]);

  return {
    ...(isSignedIn && result?.key === key ? result : {}),
    retry: () => setRequest((previous) => previous + 1),
  };
}
