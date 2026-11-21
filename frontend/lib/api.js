export async function apiFetch(getToken, path, options = {}) {
  const baseUrl = new URL(process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000");
  const url = new URL(path, baseUrl);

  if (url.origin !== baseUrl.origin) {
    throw new Error("API requests must stay on the configured FastAPI origin.");
  }

  const token = await getToken();
  if (!token) throw new Error("A Clerk session is required.");

  const headers = new Headers(options.headers);
  headers.set("Authorization", `Bearer ${token}`);

  return fetch(url, {
    ...options,
    headers,
    cache: "no-store",
    credentials: "omit",
    redirect: "error",
  });
}
