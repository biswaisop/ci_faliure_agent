import { Terminal } from "lucide-react";
import { GithubIcon } from "hugeicons-react";
import Link from "next/link";
import { Button } from "@/components/ui/button";

export const PROJECT_URL = "https://github.com/biswaisop/ci_faliure_agent";

export function Navbar() {
  return (
    <header className="site-header">
      <div className="shell nav-inner">
        <Link href="/" className="brand">
          <span className="brand-mark"><Terminal aria-hidden="true" size={17} /></span>
          AutopCI
        </Link>
        <nav aria-label="On this page">
          <a href="#workflow">How it works</a>
          <a href="#diagnosis">Sample report</a>
        </nav>
        <div className="flex items-center gap-2">
          <Button asChild variant="outline">
            <a href={PROJECT_URL}><GithubIcon aria-hidden="true" size={17} /> GitHub</a>
          </Button>
          <Button asChild>
            <Link href="/sign-in" prefetch={false}>Sign in</Link>
          </Button>
        </div>
      </div>
    </header>
  );
}
