import re
from urllib.parse import urlsplit

from app.db.models.workflow_run import WorkflowRunMetadata
from app.schema.diagnosis import DiagnosisResult
from app.schema.failure_classification import FailureClassification
from app.schema.fix_suggestion import FixSuggestion
from app.schema.investigation_context import InvestigationContext
from app.services.github_webhook_event import RepositoryLocator

SENSITIVE_TEXT = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\b(?:github_pat_|gh[pousr]_|sk-|nvapi-)[A-Za-z0-9_-]+|"
    r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|"
    r"\b(?:authorization|bearer|password|passwd|secret|api[_ -]?key|token)\s*[:= ]\s*\S+|"
    r"://[^\s/]+:[^\s/]+@",
    re.IGNORECASE,
)


def comment_marker(run: WorkflowRunMetadata, pr_number: int) -> str:
    return f"<!-- autopci:investigation:v1:{run.github_repo_id}:{run.run_id}:{run.run_attempt}:{pr_number} -->"


def safe_text(value: str, limit: int = 240) -> str:
    # Encode Markdown punctuation, mentions, and HTML, including control characters.
    if SENSITIVE_TEXT.search(value):
        return "[redacted]"
    return "".join(
        f"&#{ord(char)};" if char in r'''&<>`*_[]()~|#@!:+-.=\/''' else char
        for char in " ".join(value.split())[:limit] if char.isprintable()
    )


def dashboard_link(origin: str, run: WorkflowRunMetadata) -> str | None:
    try:
        parsed = urlsplit(origin)
        if (
            parsed.scheme in {"https", "http"} and parsed.hostname
            and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
            and parsed.netloc.isascii() and re.fullmatch(r"[a-zA-Z0-9.:-]+", parsed.netloc)
            and parsed.path in {"", "/"}
        ):
            return (
                f"{parsed.scheme}://{parsed.netloc}/dashboard/repositories/{run.github_repo_id}"
                f"/runs/{run.run_id}/attempts/{run.run_attempt}"
            )
    except ValueError:
        pass
    return None


def render_pr_comment(
    repo: RepositoryLocator, run: WorkflowRunMetadata, pr_number: int, diagnosis: DiagnosisResult | None,
    classification: FailureClassification, context: InvestigationContext, dashboard_origin: str,
    fix: FixSuggestion | None = None,
) -> str:
    repo_url = f"https://github.com/{repo.owner}/{repo.name}"
    run_url = f"{repo_url}/actions/runs/{run.run_id}/attempts/{run.run_attempt}"
    lines = [
        comment_marker(run, pr_number),
        "### AutoPCI — CI Failure Investigation",
        f"**Workflow** {safe_text(run.workflow_name or 'GitHub Actions', 100)}  ",
        f"**Status** Failed · **Commit** {safe_text(run.head_sha[:12])} · **Classification** {safe_text(classification.category)}",
        "",
        "### What failed",
        safe_text(diagnosis.summary, 300) if diagnosis else "A diagnosis is unavailable.",
    ]
    if diagnosis and diagnosis.possible_cause:
        lines.extend(["", "### Possible cause", safe_text(diagnosis.possible_cause, 300)])
    elif diagnosis:
        lines.extend(["", "The underlying cause remains unconfirmed."])
    if fix and fix.status == "suggested":
        lines.extend([
            "", "### Suggested fix", "**AI-generated suggestion (untested and unverified)**",
            safe_text(fix.summary, 300),
            *(f"- **{safe_text(change.path, 120)}** · {safe_text(change.explanation, 200)}" for change in fix.changes),
        ])
    actions = fix.tests_to_run if fix and fix.tests_to_run else diagnosis.suggested_actions if diagnosis else []
    if actions:
        lines.extend(["", "### Recommended validation", *(
            f"- {safe_text(action, 150)}" for action in actions[:3]
        )])
    links = [f"[Workflow attempt]({run_url})"]
    evidence_ids = set(diagnosis.evidence_ids) if diagnosis else set()
    for packet in context.failure_packets[:20]:
        if f"log:{packet.job_id}" in evidence_ids:
            links.append(f"[Job {packet.job_id} log]({repo_url}/actions/runs/{run.run_id}/job/{packet.job_id})")
            if len(links) == 4:
                break
    if (
        context.diff_available and context.base_sha and re.fullmatch(r"[0-9a-f]{40}", context.base_sha)
        and re.fullmatch(r"[0-9a-f]{40}", run.head_sha)
    ):
        links.append(f"[Commit comparison]({repo_url}/compare/{context.base_sha}...{run.head_sha})")
    if dashboard := dashboard_link(dashboard_origin, run):
        links.append(f"[AutoPCI dashboard]({dashboard})")
    lines.extend(["", "### Evidence and limitations", " · ".join(links)])
    lines.append(f"Commit comparison status is {safe_text(context.comparison_status.replace('_', ' '))}.")
    if diagnosis:
        lines.extend(f"- {safe_text(item, 150)}" for item in diagnosis.limitations[:2])
    return "\n".join(lines)
