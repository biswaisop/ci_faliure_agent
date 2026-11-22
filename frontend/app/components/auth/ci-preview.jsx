"use client";

import { Check, CircleX, GitBranch, GitCommitHorizontal, ScanLine, Terminal, Workflow } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";

const steps = ["Failure identified", "Commit diff analyzed", "Root cause detected"];

export function CiPreview() {
  const reduceMotion = useReducedMotion();

  return (
    <figure className="mt-8 overflow-hidden rounded-md border border-line bg-panel shadow-[0_18px_45px_#0004]">
      <figcaption className="flex flex-wrap items-center justify-between gap-2 border-b border-line bg-raised px-4 py-3">
        <span className="flex items-center gap-2 text-xs font-medium">
          <Workflow className="text-accent" size={15} aria-hidden="true" />
          GitHub Actions <span className="text-muted">/</span> Python CI
        </span>
        <span className="rounded-sm border border-line px-2 py-0.5 font-mono text-[10px] text-muted">Example run #241</span>
      </figcaption>
      <div className="px-4 py-4 sm:px-5">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[11px] text-muted">
          <span>demo-repository</span>
          <span className="flex items-center gap-1.5"><GitBranch size={13} aria-hidden="true" />main</span>
          <span className="ml-auto flex items-center gap-1.5 text-failure"><CircleX size={13} aria-hidden="true" />Failed</span>
        </div>
        <div className="mt-4 rounded-sm border border-line bg-background px-3.5 py-3 font-mono text-[11px] leading-5">
          <p className="mb-1.5 flex items-center gap-2">
            <Terminal size={14} className="text-muted" aria-hidden="true" />
            Run tests <span className="text-muted">/</span> pytest
          </p>
          <code className="wrap-break-word text-failure">ImportError: cannot import name &apos;User&apos;</code>
        </div>
        <div className="mt-4 border-t border-line pt-4">
          <p className="flex items-center gap-2 text-xs font-medium">
            <ScanLine size={15} className="text-accent" aria-hidden="true" /> AI investigation
          </p>
          <ul className="mt-2.5 flex flex-wrap gap-x-4 gap-y-2">
            {steps.map((step, index) => (
              <motion.li
                key={step}
                initial={{ opacity: 0.55 }}
                animate={{ opacity: 1 }}
                transition={{ duration: reduceMotion ? 0 : 0.35, delay: reduceMotion ? 0 : 0.18 + index * 0.13 }}
                className="flex items-center gap-1.5 text-[11px] text-muted"
              >
                <Check size={13} className="text-accent" aria-hidden="true" /> {step}
              </motion.li>
            ))}
          </ul>
          <div className="mt-3.5 rounded-sm border-l-2 border-accent bg-accent-soft px-3 py-2.5">
            <p className="flex items-center gap-1.5 font-mono text-[11px] text-accent">
              <GitCommitHorizontal size={13} aria-hidden="true" /> Suggested root cause · 7f3c9a1
            </p>
            <p className="mt-1 text-xs leading-5">
              The commit moved <code>User</code> to <code>app.models.user</code>. The tests still import it from <code>app.models</code>.
            </p>
          </div>
        </div>
      </div>
    </figure>
  );
}
