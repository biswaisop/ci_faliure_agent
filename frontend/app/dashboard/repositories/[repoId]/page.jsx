import { Suspense } from "react";
import { RepositoryRuns } from "./repository-runs";

export const metadata = { title: "Workflow runs" };

async function Runs({ params }) {
  const { repoId } = await params;
  return <RepositoryRuns repoId={repoId} />;
}

export default function RepositoryPage({ params }) {
  return (
    <Suspense fallback={<p role="status" className="text-sm text-muted">Loading workflow runs…</p>}>
      <Runs params={params} />
    </Suspense>
  );
}
