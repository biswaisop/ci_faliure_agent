import { ArrowDown, ArrowUpRight, GitBranch } from "lucide-react";
import { Diagnosis } from "@/components/diagnosis";
import { Button } from "@/components/ui/button";

export function Hero() {
  return (
    <section aria-labelledby="hero-title" className="hero">
      <div className="shell hero-inner">
        <div className="hero-copy">
          <p className="hero-context"><GitBranch aria-hidden="true" size={14} /> Built for GitHub Actions</p>
          <h1 id="hero-title">Find out why<br /><span>your CI failed.</span></h1>
          <p className="hero-description">
            Trace a failed pytest run to the line that broke it. Follow the job log,
            commit diff, and suggested fix in one report.
          </p>
          <div className="hero-actions">
            <Button asChild>
              <a href="#diagnosis">Explore sample report <ArrowDown aria-hidden="true" size={16} /></a>
            </Button>
            <Button asChild variant="outline">
              <a href="#workflow">How it works <ArrowUpRight aria-hidden="true" size={16} /></a>
            </Button>
          </div>
        </div>
        <div className="report-stage">
          <div className="stage-caption">
            <span><i aria-hidden="true" /> Interactive report</span>
            <span>EXAMPLE RUN / #241</span>
          </div>
          <Diagnosis />
        </div>
      </div>
    </section>
  );
}
