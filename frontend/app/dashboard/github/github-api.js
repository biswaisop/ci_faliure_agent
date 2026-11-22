import { apiFetch } from "../../../lib/api";

export async function githubRequest(getToken, endpoint, body, signal) {
  const timeout = AbortSignal.timeout(15000);
  const response = await apiFetch(getToken, `/api/github/installations${endpoint}`, {
    method: body ? "POST" : "GET",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
  });
  const data = await response.json().catch(() => null);
  if (!response.ok || !data) {
    throw new Error(
      typeof data?.detail === "string" ? data.detail : `The GitHub request failed (HTTP ${response.status}).`,
    );
  }
  return data;
}

export async function authorizeGithub(getToken, body) {
  const { url } = await githubRequest(getToken, "/authorize", body);
  window.location.assign(url);
}
