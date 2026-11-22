import { GitFork } from "lucide-react";
import { Suspense } from "react";
import { GithubCallback } from "./github-callback";

export const metadata = {
  title: "Connect GitHub",
  referrer: "no-referrer",
};

export default function GithubCallbackPage() {
  return (
    <section aria-labelledby="connect-heading" className="w-full max-w-xl rounded-md border border-line bg-panel p-5 sm:p-8">
      <span className="mb-4 grid size-10 place-items-center rounded-md border border-line bg-accent-soft text-accent">
        <GitFork size={21} aria-hidden="true" />
      </span>
      <h1 id="connect-heading" className="text-2xl font-semibold tracking-tight">Connect GitHub</h1>
      <Suspense fallback={<p role="status" className="mt-3 text-sm text-muted">Loading GitHub setup…</p>}>
        <GithubCallback />
      </Suspense>
    </section>
  );
}
