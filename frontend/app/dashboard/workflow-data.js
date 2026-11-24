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

export function useWorkflowData(path, cache) {
  const { getToken, isSignedIn, sessionId } = useAuth();
  const [request, setRequest] = useState(0);
  const [result, setResult] = useState(null);
  const cacheKey = `${sessionId}:${path}`;
  const key = `${cacheKey}:${request}`;

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

    async function load(useCache = false) {
      if (inFlight) return;
      inFlight = true;
      try {
        const cached = useCache ? cache?.get(cacheKey) : undefined;
        if (cached && cached.expiresAt > Date.now()) {
          data = cached.data;
        } else {
          const response = await apiFetch(getToken, `${apiPath}${path}`, {
            signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
          });
          if (controller.signal.aborted) return;
          if ([401, 403, 404, 422].includes(response.status)) {
            data = null;
            cache?.delete(cacheKey);
            setResult({ key, error: "This resource is unavailable for your session." });
            return;
          }
          if (!response.ok) throw new Error("Workflow request failed");
          data = await response.json();
          if (controller.signal.aborted) return;
          cache?.set(cacheKey, { data, expiresAt: Date.now() + (shouldPoll(data) ? 5000 : 30000) });
        }
        setResult({ key, data });
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
    load(true);
    return () => {
      controller.abort();
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", syncVisibility);
    };
  }, [cache, cacheKey, getToken, isSignedIn, key, path]);

  return {
    ...(isSignedIn && result?.key === key ? result : {}),
    retry: () => {
      cache?.delete(cacheKey);
      setRequest((previous) => previous + 1);
    },
  };
}
