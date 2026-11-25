import base64
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from app.agent.investigation_agent import execute_investigation
from app.agent.tools.investigation import InvestigationTools
from app.schema.investigation import InvestigationLimits, ToolRequest
from app.services.failure_classifier import classify_failure
from app.services.gh_collector import GitHubCollector
from app.services.github_webhook_event import RepositoryLocator
from app.services.log_reducer import reduce_job_log
from tests.test_investigation_agent import (
    PAGES_ERROR,
    PAGES_LOG,
    FakeLLM,
    finding,
    make_context,
)

pytestmark = pytest.mark.anyio
REPO = RepositoryLocator(owner="biswaisop", name="portFolio", github_repo_id=100)
WORKFLOW = ".github/workflows/nextjs.yml"
NEXT_CONFIG = "next.config.ts"
FILES = {
    WORKFLOW: "run: npm run build\n    uses: actions/configure-pages@v5\n",
    NEXT_CONFIG: "export default { reactStrictMode: true }\n",
    "app.py": "def calculate():\n    return 2\n",
}


def repo_api(request):
    path = request.url.path
    assert request.method == "GET" and path.startswith("/repos/biswaisop/portFolio/git/")
    assert request.headers["Authorization"] == "Bearer installation-token"
    if path.endswith("/commits/" + "a" * 40):
        return httpx.Response(200, json={"sha": "a" * 40, "tree": {"sha": "b" * 40}})
    if path.endswith("/trees/" + "b" * 40):
        return httpx.Response(200, json={"tree": [
            {"path": name, "sha": str(index) * 40, "type": "blob", "mode": "100644", "size": len(text)}
            for index, (name, text) in enumerate(FILES.items(), 1)
        ]})
    for index, source in enumerate(FILES.values(), 1):
        sha = str(index) * 40
        if path.endswith("/blobs/" + sha):
            return httpx.Response(200, json={
                "sha": sha, "encoding": "base64", "content": base64.b64encode(source.encode()).decode(),
            })
    pytest.fail(f"Unexpected GitHub request {path}")


async def test_pages_specialist_reads_only_authorized_workflow_and_config():
    packet = reduce_job_log(PAGES_LOG, 123, "Deploy Next.js site to Pages", "Setup Pages")
    context = make_context(packet, workflow_path=WORKFLOW)
    ids = [f"workflow:{WORKFLOW}@{context.head_sha}", f"source:{NEXT_CONFIG}@{context.head_sha}"]
    model = FakeLLM(
        [
            ToolRequest(name="read_workflow_file", path=WORKFLOW),
            ToolRequest(name="read_repository_file", path=NEXT_CONFIG),
            ToolRequest(name="read_workflow_file", path=".github/workflows/other.yml"),
            ToolRequest(name="read_repository_file", path="app.py"),
        ],
        finding("github_actions", packet, evidence_ids=["log:123", *ids], investigated_files=[WORKFLOW, NEXT_CONFIG]),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(repo_api)) as client:
        tools = InvestigationTools(
            context, InvestigationLimits(), github=GitHubCollector(client=client), repo=REPO, token="installation-token",
        )
        result = await execute_investigation(context=context, classification=classify_failure(context), llm=model, tools=tools)
    assert result.consulted == ["github_actions"]
    assert result.diagnosis.evidence_ids == ["log:123", *ids]
    assert result.diagnosis.summary == PAGES_ERROR and result.diagnosis.possible_cause is None
    assert result.usage.tool_calls == 4 and result.usage.github_calls == 4
    assert result.sources == {path: FILES[path] for path in [WORKFLOW, NEXT_CONFIG]}
    assert "installation-token" not in repr(model.prompts)
    assert {"evidence_id": ids[0], "evidence": FILES[WORKFLOW]} in model.transcripts[1]


@pytest.mark.parametrize("path", ["../app.py", "/app.py", "C:/app.py", "src\\app.py", "%2e%2e/app.py",
                                 ".env", "src/secrets.py", ".github/workflows/other.yml"])
async def test_invalid_paths_are_rejected_without_network(path):
    context = make_context()
    async with httpx.AsyncClient(transport=httpx.MockTransport(repo_api)) as client:
        tools = InvestigationTools(
            context, InvestigationLimits(), github=GitHubCollector(client=client), repo=REPO, token="installation-token",
        )
        reply = await tools.invoke(ToolRequest(name="read_repository_file", path=path), "python")
    assert "outside" in reply["unavailable"] and tools.usage.github_calls == 0


async def test_tool_cannot_override_repository_or_revision():
    context = make_context()
    with pytest.raises(ValueError, match="repository mismatch"):
        InvestigationTools(context, InvestigationLimits(), repo=REPO.model_copy(update={"github_repo_id": 200}))
    with pytest.raises(ValidationError):
        ToolRequest.model_validate({"name": "read_repository_file", "path": "app.py", "sha": "main", "owner": "other"})
    context = context.model_copy(update={"head_sha": "main"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(repo_api)) as client:
        tools = InvestigationTools(
            context, InvestigationLimits(), github=GitHubCollector(client=client), repo=REPO, token="installation-token",
        )
        reply = await tools.invoke(ToolRequest(name="read_repository_file", path="app.py"), "python")
    assert "immutable" in reply["unavailable"] and tools.usage.github_calls == 0


@pytest.mark.parametrize(("limits", "path", "calls", "why"), [
    (InvestigationLimits(), "missing.py", 2, "absent"),
    (InvestigationLimits(file_bytes=10), "app.py", 2, "oversized"),
    (InvestigationLimits(github_calls=1), "app.py", 1, "GitHub reads"),
    (InvestigationLimits(source_reads=0), "app.py", 0, "source-read budget"),
    (InvestigationLimits(output_bytes=10), "app.py", 3, "tool-output budget"),
    (InvestigationLimits(tool_calls=0), "app.py", 0, "tool-call budget"),
])
async def test_file_and_output_limits(limits, path, calls, why):
    async with httpx.AsyncClient(transport=httpx.MockTransport(repo_api)) as client:
        tools = InvestigationTools(
            make_context(), limits, github=GitHubCollector(client=client), repo=REPO, token="installation-token",
        )
        reply = await tools.invoke(ToolRequest(name="read_repository_file", path=path), "python")
    assert why in reply["unavailable"] and tools.usage.github_calls == calls and not tools.evidence


async def test_rate_limit_stops_additional_github_calls():
    def rate_limit(request):
        return httpx.Response(403, headers={"x-ratelimit-remaining": "0"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(rate_limit)) as client:
        tools = InvestigationTools(
            make_context(), InvestigationLimits(), github=GitHubCollector(client=client), repo=REPO, token="scoped",
        )
        reply = await tools.invoke(ToolRequest(name="read_repository_file", path="app.py"), "python")
        assert "rate limited" in reply["unavailable"]
        await tools.invoke(ToolRequest(name="read_repository_file", path="different.py"), "python")
    assert tools.usage.github_calls == 1


async def test_job_excerpt_ids_stay_within_workflow_and_line_budget():
    packet = reduce_job_log("one\ntwo\nthree\nAssertionError: failure", 123, "pytest")
    tools = InvestigationTools(make_context(packet), InvestigationLimits(log_lines=1))
    reply = await tools.invoke(ToolRequest(name="get_failed_job_details", job_id=99), "python")
    assert "outside" in reply["unavailable"] and tools.usage.github_calls == 0
    reply = await tools.invoke(ToolRequest(name="get_job_log_excerpt", job_id=123, start_line=2), "python")
    assert reply["evidence_id"] == "log:123:2-2"
    assert tools.evidence["log:123:2-2"] == "two"


async def test_history_is_bounded_and_does_not_follow_pagination_links():
    def history(request):
        assert request.url.host == "api.github.com"
        assert request.url.path.endswith("/workflows/300/runs")
        assert request.url.params["per_page"] == "4"
        assert request.url.params["created"].startswith("<=")
        return httpx.Response(
            200, headers={"Link": '<https://other.example/page/2>; rel="next"'},
            json={"workflow_runs": [{"id": 199, "workflow_id": 300, "conclusion": "success"}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(history)) as client:
        tools = InvestigationTools(
            make_context(), InvestigationLimits(), github=GitHubCollector(client=client), repo=REPO, token="scoped",
            failed_at=datetime(2026, 11, 24, tzinfo=UTC),
        )
        reply = await tools.invoke(ToolRequest(name="get_run_history_summary", limit=4), "github_actions")
    assert reply == {"evidence_id": "history:300"} and tools.usage.github_calls == 1
