import { FileCode2, GitPullRequest, Terminal } from "lucide-react";

const steps = [
  {
    title: "Start at the failure",
    description: "Find the failed job and the test output that explains what broke.",
    Icon: Terminal,
    preview: ["$ pytest tests/test_checkout.py", "FAILED  test_empty_cart", "E  AssertionError: assert 200 == 422"],
  },
  {
    title: "Compare the evidence",
    description: "Read the commit diff alongside recent runs to see what changed and when.",
    Icon: FileCode2,
    preview: ["checkout.py · line 61", "-    if not cart.items:", "-        raise EmptyCartError()"],
  },
  {
    title: "Bring it to the PR",
    description: "Use the cited evidence to review the likely cause and decide what to rerun.",
    Icon: GitPullRequest,
    preview: ["Suggested next step", "Restore the empty-cart guard.", "Rerun test_empty_cart."],
  },
];

export function Workflow() {
  return (
    <section id="workflow" tabIndex={-1} aria-labelledby="workflow-title" className="shell workflow">
      <div className="workflow-heading">
        <h2 id="workflow-title">From failed run<br />to diagnosis.</h2>
        <p>See how the sample report connects the failing assertion to a specific change in the pull request.</p>
      </div>
      <ol className="workflow-grid">
        {steps.map(({ title, description, Icon, preview }, index) => (
          <li key={title} className="workflow-card">
            <div className="workflow-card-top">
              <Icon aria-hidden="true" size={21} />
              <span aria-hidden="true">0{index + 1}</span>
            </div>
            <h3>{title}</h3>
            <p>{description}</p>
            <div className="workflow-preview">
              {preview.map((line) => <code key={line}>{line}</code>)}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
