import assert from "node:assert/strict";
import test from "node:test";
import { apiFetch } from "./api.js";

test("sends a fresh Clerk token to FastAPI and preserves a pending profile response", async (t) => {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL;
  process.env.NEXT_PUBLIC_API_URL = "https://api.example.test";
  t.after(() => {
    if (apiUrl === undefined) delete process.env.NEXT_PUBLIC_API_URL;
    else process.env.NEXT_PUBLIC_API_URL = apiUrl;
  });

  const responses = [404, 200];
  const getToken = t.mock.fn(async () => `test-token-${responses.length}`);
  const fetchMock = t.mock.method(globalThis, "fetch", async (url, options) => {
    assert.equal(url.href, "https://api.example.test/api/users/me");
    assert.equal(options.headers.get("Authorization"), `Bearer test-token-${responses.length}`);
    assert.equal(options.redirect, "error");
    return new Response(null, { status: responses.shift() });
  });

  assert.equal((await apiFetch(getToken, "/api/users/me")).status, 404);
  assert.equal((await apiFetch(getToken, "/api/users/me")).status, 200);
  await assert.rejects(apiFetch(getToken, "https://other.example.test/api/users/me"));
  await assert.rejects(apiFetch(async () => null, "/api/users/me"));
  assert.equal(getToken.mock.callCount(), 2);
  assert.equal(fetchMock.mock.callCount(), 2);
});
