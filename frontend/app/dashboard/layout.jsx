import { ChevronRight, GitFork, GitPullRequest, UserRound } from "lucide-react";
import Link from "next/link";
import { Brand } from "@/components/brand";

const navClass = "flex min-h-10 items-center gap-2.5 rounded-sm px-3 text-sm font-medium text-muted hover:bg-accent-soft hover:text-accent";

export default function WorkspaceLayout({ children }) {
  return (
    <div className="min-h-dvh">
      <header className="flex h-16 items-center gap-3 border-b border-line px-5 sm:gap-4 sm:px-7">
        <Brand />
        <ChevronRight size={15} className="text-muted" aria-hidden="true" />
        <Link href="/dashboard" className="text-sm font-medium text-muted hover:text-accent">
          Workspace
        </Link>
      </header>
      <div className="min-h-[calc(100dvh-4rem)] md:grid md:grid-cols-[13rem_minmax(0,1fr)] lg:grid-cols-[15rem_minmax(0,1fr)]">
        <aside className="border-b border-line bg-panel px-3 py-2 md:border-r md:border-b-0 md:p-4">
          <nav aria-label="Workspace" className="flex flex-wrap gap-1 md:sticky md:top-4 md:flex-col">
            <Link className={navClass} href="/dashboard#repositories">
              <GitPullRequest size={17} aria-hidden="true" /> Repositories
            </Link>
            <Link className={navClass} href="/dashboard#installations">
              <GitFork size={17} aria-hidden="true" /> GitHub installations
            </Link>
            <Link className={navClass} href="/dashboard#account">
              <UserRound size={17} aria-hidden="true" /> Your account
            </Link>
          </nav>
        </aside>
        <main className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12 lg:px-12">
          {children}
        </main>
      </div>
    </div>
  );
}
