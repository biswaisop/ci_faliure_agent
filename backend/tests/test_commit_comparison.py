from datetime import UTC, datetime

import httpx
import pytest

from app.schema.investigation_context import InvestigationContext
from app.services.gh_collector import GitHubCollector
from app.services.investigation_context_builder import build_investigation_context

pytestmark = pytest.mark.anyio

FAILED_AT = datetime(2026, 11, 23, 8, tzinfo=UTC)
HISTORY = "/repos/owner/repo/actions/workflows/2/runs"
COMPARE = "/repos/owner/repo/compare/base...head"


def successful_run(**fields):
    return {
        "id": 95, "workflow_id": 2, "head_branch": "main", "head_sha": "base",
        "head_repository": {"id": 7}, "conclusion": "success", "status": "completed",
        "run_attempt": 1, "created_at": "2026-11-23T05:00:00Z", "updated_at": "2026-11-23T06:00:00Z",
        **fields,
    }


async def resolve(handler, **options):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await GitHubCollector(client=client).resolve_commit_comparison(
            "owner", "repo", workflow_id=2, branch="main", run_id=40, head_sha="head", head_repository_id=7,
            token="test-installation-token", **{"run_attempt": 1, "failed_at": FAILED_AT, **options},
        )


def comparison(**fields):
    return {"status": "ahead", "ahead_by": 1, "behind_by": 0, "merge_base_commit": {"sha": "base"},
            "files": [{"filename": "app.py", "status": "modified", "patch": "@@ -1 +1 @@\n-old\n+new"}], **fields}


async def test_uses_completion_order_and_verifies_ancestry():
    def handler(request):
        assert request.headers["Authorization"] == "Bearer test-installation-token"
        if request.url.path == HISTORY:
            assert request.url.params["branch"] == "main"
            return httpx.Response(200, json={"workflow_runs": [
                successful_run(id=91, head_sha="later", updated_at="2026-11-23T08:30:00Z"),
                successful_run(id=94, head_sha="older", created_at="2026-11-23T05:30:00Z", updated_at="2026-11-23T05:45:00Z"),
                successful_run(),
            ]})
        assert request.url.path == COMPARE
        assert request.url.params["page"] == "1"
        return httpx.Response(200, json=comparison())

    result = await resolve(handler)
    assert result.status == "available" and result.base_sha == "base"
    context = build_investigation_context(
        github_repo_id=7, run_id=40, run_attempt=1, workflow_id=2, head_branch="main", head_sha="head",
        base_sha=result.base_sha, failure_packets=[], diff=result.diff, comparison_status=result.status,
    )
    serialized = context.model_dump(mode="json")
    assert serialized["changed_files"][0]["patch"].endswith("+new")
    assert serialized["comparison_status"] == "available" and serialized["diff_available"]
    assert InvestigationContext.model_validate(serialized) == context
    for field in ("comparison_status", "comparison_reason", "files_limit_reached"):
        serialized.pop(field)
    legacy = InvestigationContext.model_validate(serialized)
    assert legacy.comparison_status == "not_attempted" and legacy.changed_files == context.changed_files


@pytest.mark.parametrize("runs", [[], [successful_run(head_branch="feature"), successful_run(head_repository={"id": 9})]])
async def test_no_previous_success_on_the_right_branch_and_repository(runs):
    def handler(request):
        assert request.url.path == HISTORY
        return httpx.Response(200, json={"workflow_runs": runs})

    assert (await resolve(handler)).status == "no_previous_success"


async def test_same_commit_success_on_previous_attempt_of_the_same_run():
    def handler(request):
        if request.url.path == HISTORY:
            return httpx.Response(200, json={"workflow_runs": [successful_run(id=40, run_attempt=3)]})
        assert request.url.path.endswith("/runs/40/attempts/1")
        return httpx.Response(200, json=successful_run(id=40, head_sha="head"))

    result = await resolve(handler, run_attempt=2)
    assert (result.status, result.base_sha, result.diff) == ("same_commit", "head", None)


async def test_recovers_earlier_success_hidden_by_a_later_rerun():
    def handler(request):
        if request.url.path == HISTORY:
            return httpx.Response(200, json={"workflow_runs": [successful_run(run_attempt=2, conclusion="failure")]})
        if request.url.path.endswith("/runs/95/attempts/1"):
            return httpx.Response(200, json=successful_run())
        assert request.url.path == COMPARE
        return httpx.Response(200, json=comparison())

    assert (await resolve(handler)).status == "available"


@pytest.mark.parametrize("compare_result, status, reason", [
    (httpx.Response(200, json=comparison(status="diverged", behind_by=1)), "baseline_not_ancestor", None),
    (httpx.Response(200, json=comparison(merge_base_commit={"sha": "older"})), "baseline_not_ancestor", None),
    (httpx.Response(403), "comparison_failed", "github_access_denied"),
    (httpx.Response(403, headers={"X-RateLimit-Remaining": "0"}), "comparison_failed", "github_rate_limited"),
    (httpx.Response(404), "comparison_failed", "commit_unavailable"),
    (httpx.Response(422), "comparison_failed", "commit_unavailable"),
    (httpx.Response(502), "comparison_failed", "github_unavailable"),
])
async def test_failed_or_unrelated_comparison_retains_no_diff(compare_result, status, reason):
    def handler(request):
        if request.url.path == HISTORY:
            return httpx.Response(200, json={"workflow_runs": [successful_run()]})
        return compare_result

    result = await resolve(handler)
    assert (result.status, result.reason, result.diff) == (status, reason, None)


@pytest.mark.parametrize("count, with_patch", [(1, False), (300, True)])
async def test_reports_omitted_patches_and_github_file_cap(count, with_patch):
    files = [
        {"filename": f"{index}.py", "status": "modified", **({"patch": "+line"} if with_patch else {})}
        for index in range(count)
    ]

    def handler(request):
        if request.url.path == HISTORY:
            return httpx.Response(200, json={"workflow_runs": [successful_run()]})
        return httpx.Response(200, json=comparison(files=files))

    result = await resolve(handler)
    assert result.status == "partial"
    assert result.diff["files_limit_reached"] is (count == 300)
    file = result.diff["files"][0]
    assert file["patch_status"] == ("available" if with_patch else "omitted")
    assert file["truncated"] is False
