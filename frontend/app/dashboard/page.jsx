import { auth } from "@clerk/nextjs/server";
import { Suspense } from "react";
import { Dashboard, DashboardLoading } from "./dashboard";

export const metadata = { title: "Dashboard | CI Postmortem Agent" };

async function ProtectedDashboard() {
  const { isAuthenticated, redirectToSignIn, sessionId } = await auth();
  if (!isAuthenticated) return redirectToSignIn();

  return <Dashboard key={sessionId} />;
}

export default function DashboardPage() {
  return (
    <Suspense fallback={<DashboardLoading />}>
      <ProtectedDashboard />
    </Suspense>
  );
}
