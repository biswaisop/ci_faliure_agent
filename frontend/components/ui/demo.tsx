import { GitBranch, GitPullRequest, Terminal } from "lucide-react";
import { GithubIcon } from "hugeicons-react";
import { Hero } from "@/components/hero";
import { Navbar, PROJECT_URL } from "@/components/navbar";
import { Button } from "@/components/ui/button";
import { Workflow } from "@/components/workflow";

export default function LandingDemo() {
  return (
    <>
      <Navbar />
      <main>
        <Hero />
        <div className="integration-strip">
          <ul className="shell">
            <li><GitBranch aria-hidden="true" size={17} /> GitHub Actions</li>
            <li><Terminal aria-hidden="true" size={17} /> Python / pytest</li>
            <li><GitPullRequest aria-hidden="true" size={17} /> Pull request triage</li>
          </ul>
        </div>
        <Workflow />
        <section className="shell project-section" aria-labelledby="project-title">
          <div className="project-card">
            <div>
              <p>Project in development</p>
              <h2 id="project-title">Follow AutopCI on GitHub.</h2>
              <p>Browse the source for the webhook and evidence collector.</p>
            </div>
            <Button asChild variant="outline">
              <a href={PROJECT_URL}><GithubIcon aria-hidden="true" size={17} /> View repository</a>
            </Button>
          </div>
        </section>
      </main>
      <footer className="site-footer">
        <div className="shell">
          <span className="brand"><span className="brand-mark"><Terminal aria-hidden="true" size={17} /></span> AutopCI</span>
          <a href={PROJECT_URL}>Source on GitHub</a>
        </div>
      </footer>
    </>
  );
}
