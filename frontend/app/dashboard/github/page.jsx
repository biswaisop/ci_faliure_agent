import { Suspense } from "react";
import { GithubCallback } from "./github-callback";

export const metadata = {
  title: "Connect GitHub | CI Postmortem Agent",
  referrer: "no-referrer",
};

export default function GithubCallbackPage() {
  return (
    <main className="auth-shell grid min-h-dvh place-items-center bg-[#09090b] px-5 py-10 text-[#fafafa]">
      <section className="w-full max-w-md rounded-xl border border-[#27272a] bg-[#111114] p-6 sm:p-8">
        <h1 className="text-2xl font-semibold tracking-tight">Connect GitHub</h1>
        <Suspense fallback={<p role="status" className="mt-3 text-sm text-[#a1a1aa]">Loading GitHub setup…</p>}>
          <GithubCallback />
        </Suspense>
      </section>
    </main>
  );
}
