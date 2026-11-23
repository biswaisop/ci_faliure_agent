import { Suspense } from "react";
import { Investigation } from "@/app/dashboard/investigation";

export const metadata = { title: "Failure investigation" };

async function Attempt({ params }) {
  const { repoId, runId, attempt } = await params;
  return <Investigation repoId={repoId} runId={runId} attempt={attempt} />;
}

export default function InvestigationPage({ params }) {
  return (
    <Suspense fallback={<p role="status" className="text-sm text-muted">Loading investigation…</p>}>
      <Attempt params={params} />
    </Suspense>
  );
}
