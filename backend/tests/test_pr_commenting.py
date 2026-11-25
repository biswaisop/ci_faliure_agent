import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pymongo.errors import AutoReconnect

from app.db.models.workflow_run import PullRequestHint, WorkflowRunMetadata
from app.schema.diagnosis import DiagnosisResult
from app.schema.failure_classification import FailureClassification
from app.schema.fix_suggestion import FixSuggestion, SuggestedFileChange
from app.schema.investigation_context import InvestigationContext
from app.services import post_investigation
from app.services.github_pr_resolver import resolve_pull_request
from app.services.github_webhook_event import RepositoryLocator
from app.services.pr_comment_publisher import PRCommentPublisher, github_failure
from app.services.pr_comment_renderer import comment_marker, render_pr_comment

HEAD = "a" * 40
MERGE = "b" * 40
REPO = RepositoryLocator(owner="sample", name="project", github_repo_id=10)
CLASSIFICATION = FailureClassification(category="unknown", confidence=0.2, reasons=[])
CONTEXT = InvestigationContext(github_repo_id=10, run_id=11, run_attempt=1, workflow_id=12, head_sha=HEAD)
DIAGNOSIS = DiagnosisResult(summary="Observed failure", root_cause="Unknown", confidence="high", evidence_validated=True)


def workflow(**fields):
    return WorkflowRunMetadata.model_validate({
        "github_repo_id": 10, "run_id": 11, "run_attempt": 1, "workflow_id": 12, "installation_id": 13,
        "head_sha": HEAD, "head_branch": "feature", "event": "pull_request", "github_head_repo_id": 10, **fields,
    })


def pr_api(*, base=10, head=10, associated=None):
    return SimpleNamespace(
        path="/repos/sample/project",
        associated_pull_requests=AsyncMock(return_value=[4] if associated is None else associated),
        get_pull_request=AsyncMock(return_value={
            "number": 4, "state": "open", "base": {"repo": {"id": base}},
            "head": {"repo": {"id": head}, "sha": HEAD, "ref": "feature"}, "merge_commit_sha": MERGE,
        }),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("event,sha,head,base,associated,hinted,expected", [
    ("pull_request", HEAD, 10, 10, [4], True, "resolved"),
    ("pull_request", MERGE, 10, 10, [4], True, "resolved"),
    ("pull_request", MERGE, 22, 10, [4], True, "resolved"),
    ("pull_request", HEAD, 22, 10, [4], False, "resolved"),
    ("pull_request", MERGE, 22, 10, [4], False, "no_pr_found"),
    ("pull_request", HEAD, 10, 33, [4], True, "no_pr_found"),
    ("push", HEAD, 10, 10, [], False, "no_pr_found"),
    ("push", HEAD, 10, 10, [4], False, "resolved"),
    ("push", HEAD, 10, 10, [4, 5], False, "ambiguous_pr"),
])
async def test_pr_association(event, sha, head, base, associated, hinted, expected):
    run = workflow(event=event, head_sha=sha, github_head_repo_id=head, pull_requests=[
        PullRequestHint(number=4, base_repo_id=10, head_repo_id=head, head_sha=HEAD),
    ] if hinted else [])
    assert (await resolve_pull_request(run, REPO, pr_api(base=base, head=head, associated=associated))).status == expected


def test_renderer_delimits_untrusted_content():
    diagnosis = DIAGNOSIS.model_copy(update={
        "summary": "``` <!-- autopci:spoof --> @someone [go](https://bad.example)",
        "possible_cause": "token=ghp_FAKE_SECRET",
    })
    body = render_pr_comment(REPO, workflow(), 4, diagnosis, CLASSIFICATION, CONTEXT, "https://app.example")
    assert body.startswith(comment_marker(workflow(), 4))
    assert "```" not in body and "@someone" not in body and "<!-- autopci:spoof" not in body
    assert "FAKE_SECRET" not in body
    assert "[AutoPCI dashboard](https://app.example/" in body


class Publications:
    def __init__(self):
        self.row = {}
        self.claimed = False
        self.fail_save = False

    async def claim(self, run):
        if self.claimed:
            return None
        self.claimed = True
        return {"lease_id": "lease", **self.row}

    async def write(self, run, lease, **fields):
        self.row.update(fields)

    async def finish(self, run, lease, status, **fields):
        if self.fail_save and status == "published":
            self.fail_save = False
            raise AutoReconnect("write interrupted")
        self.row.update(fields, status=status)
        self.claimed = False


@pytest.mark.asyncio
async def test_publish_reconciles_remote_create_and_updates_only_owned_comment():
    github = pr_api()
    run = workflow()
    user_comment = {
        "id": 1, "body": comment_marker(run, 4), "user": {"type": "User", "login": "autopci"},
        "issue_url": "https://api.github.com/repos/sample/project/issues/4",
    }
    comments = [user_comment]
    created = asyncio.Event()
    release = asyncio.Event()

    async def create(number, body):
        created.set()
        await release.wait()
        comment = {**user_comment, "id": 2, "body": body, "user": {"type": "Bot", "login": "autopci[bot]"}}
        comments.append(comment)
        return comment

    async def find(number, matches):
        return next((comment for comment in comments if matches(comment)), None)

    async def update(comment_id, body):
        assert comment_id == 2
        comments[1]["body"] = body

    github.create_comment = AsyncMock(side_effect=create)
    github.find_comment = AsyncMock(side_effect=find)
    github.update_comment = AsyncMock(side_effect=update)
    github.get_comment = AsyncMock(side_effect=lambda comment_id: comments[comment_id - 1])
    store = Publications()
    store.fail_save = True
    service = PRCommentPublisher(store, "autopci", "123", "")

    async def publish(diagnosis=DIAGNOSIS, fix=None):
        return await service.publish(run, REPO, github, diagnosis, CLASSIFICATION, CONTEXT, fix)

    first = asyncio.create_task(publish())
    await created.wait()
    assert await publish() == 60
    release.set()
    assert await first == 60
    assert store.row["create_uncertain"]
    assert await publish() is None
    assert store.row["comment_id"] == 2
    assert await publish(DIAGNOSIS.model_copy(update={"summary": "New diagnosis"})) is None
    assert store.row["status"] == "updated"
    assert await publish(DIAGNOSIS.model_copy(update={"summary": "New diagnosis"})) is None
    github.create_comment.assert_awaited_once()
    github.update_comment.assert_awaited_once()
    fix = FixSuggestion(
        status="suggested", summary="Correct addition", rationale="The operation subtracts the operands.",
        changes=[SuggestedFileChange(
            path="math.py", original_snippet="return a - b", replacement_snippet="return a + b",
            explanation="Add both operands.",
        )],
    )
    assert await publish(DIAGNOSIS.model_copy(update={"summary": "New diagnosis"}), fix) is None
    assert "### Suggested fix" in comments[1]["body"]
    assert store.row["comment_id"] == 2
    assert github.create_comment.await_count == 1
    assert github.update_comment.await_count == 2


@pytest.mark.asyncio
async def test_unknown_create_outcome_never_creates_a_second_comment():
    github = pr_api()
    github.find_comment = AsyncMock(return_value=None)
    github.create_comment = AsyncMock(side_effect=httpx.ReadTimeout("write outcome unknown"))
    store = Publications()
    publisher = PRCommentPublisher(store, "autopci", "123", "")
    for _ in range(2):
        assert await publisher.publish(workflow(), REPO, github, DIAGNOSIS, CLASSIFICATION, CONTEXT) == 60
        assert store.row["status"] == "uncertain"
    github.create_comment.assert_awaited_once()
    assert github.find_comment.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status,reason", [(403, "missing_permission"), (404, "pr_unavailable")])
async def test_pr_access_failure_stops_before_comment_write(status, reason):
    github = pr_api()
    response = httpx.Response(status, request=httpx.Request("GET", "https://api.github.com"))
    github.get_pull_request.side_effect = httpx.HTTPStatusError(
        "access denied", request=response.request, response=response,
    )
    github.create_comment = AsyncMock()
    store = Publications()
    publisher = PRCommentPublisher(store, "autopci", "123", "")
    assert await publisher.publish(workflow(), REPO, github, DIAGNOSIS, CLASSIFICATION, CONTEXT) is None
    assert (store.row["status"], store.row["error_code"]) == ("failed", reason)
    github.create_comment.assert_not_awaited()


def test_rate_limit_and_permission_errors():
    def error(status, **headers):
        response = httpx.Response(status, headers=headers, request=httpx.Request("GET", "https://api.github.com"))
        return httpx.HTTPStatusError("test", request=response.request, response=response)

    assert github_failure(error(403, **{"Retry-After": "90"})) == ("rate_limited", 90)
    assert github_failure(error(429)) == ("rate_limited", 60)
    assert github_failure(error(403)) == ("missing_permission", None)
    assert github_failure(error(404)) == ("pr_unavailable", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("fix_fails,github_fails", [(False, False), (True, False), (False, True)])
async def test_followup_order_and_failure_isolation(monkeypatch, fix_fails, github_fails):
    from unittest.mock import Mock

    run = workflow()
    run = SimpleNamespace(**run.model_dump(), evidence=SimpleNamespace(
        investigation_context=CONTEXT, classification=CLASSIFICATION,
    ))
    install = SimpleNamespace(status="active", sync_version=2, repository_sync_version=2)
    repo = SimpleNamespace(
        is_active=True, installation_id=13, sync_version=2, owner=SimpleNamespace(login="sample"), name="project",
    )
    investigation = {"status": "completed", "diagnosis": DIAGNOSIS.model_dump()}
    fix = FixSuggestion(
        status="suggested", summary="Fix addition", rationale="The failure shows an incorrect result.",
        changes=[SuggestedFileChange(
            path="math.py", original_snippet="return a - b", replacement_snippet="return a + b",
            explanation="Add both operands.",
        )],
    )
    stored = None
    order = []
    rendered = []

    async def generate(*args, cached_sources=None):
        nonlocal stored
        assert cached_sources is None
        order.append("fix")
        if fix_fails:
            return None, 60
        stored = {"status": "suggested", "suggestion": fix.model_dump()}
        return fix, None

    async def publish(run, locator, github, diagnosis, classification, context, suggestion):
        order.append("comment")
        if github_fails:
            return 60
        rendered.append(render_pr_comment(locator, run, 4, diagnosis, classification, context, "", suggestion))
        return None

    monkeypatch.setattr(post_investigation.settings, "AUTO_PCI_PR_COMMENTS", True)
    monkeypatch.setattr(post_investigation.settings, "AUTO_PCI_FIX_SUGGESTIONS", True)
    monkeypatch.setattr(post_investigation.MongoDb, "investigations", Mock())
    monkeypatch.setattr(post_investigation, "WorkflowRunRepository", Mock(return_value=SimpleNamespace(
        get_run=AsyncMock(return_value=run),
    )))
    investigation_repo = SimpleNamespace(get=AsyncMock(return_value=investigation))
    monkeypatch.setattr(post_investigation, "InvestigationRepository", Mock(return_value=investigation_repo))
    monkeypatch.setattr(post_investigation, "GitHubInstallationRepository", Mock(return_value=SimpleNamespace(
        get_by_installation_id=AsyncMock(return_value=install),
    )))
    monkeypatch.setattr(post_investigation, "GitHubRepositoryRepository", Mock(return_value=SimpleNamespace(
        get_by_repo_id=AsyncMock(return_value=repo),
    )))
    token = AsyncMock(return_value="scoped")
    monkeypatch.setattr(post_investigation, "get_installation_token", token)
    monkeypatch.setattr(post_investigation, "PRCommentRepository", Mock())
    monkeypatch.setattr(post_investigation, "PRCommentPublisher", Mock(return_value=SimpleNamespace(publish=publish)))
    monkeypatch.setattr(post_investigation, "FixSuggestionService", Mock(return_value=SimpleNamespace(generate=generate)))
    monkeypatch.setattr(post_investigation, "FixSuggestionRepository", Mock(return_value=SimpleNamespace(
        get=AsyncMock(side_effect=lambda *args: stored),
    )))
    queue = AsyncMock()
    monkeypatch.setattr(post_investigation, "queue_followup", queue)

    assert await post_investigation.process_stage(workflow(), 13, "diagnosis_comment") == (
        60 if github_fails else None, None,
    )
    assert await post_investigation.process_stage(workflow(), 13, "fix") == (60 if fix_fails else None, None)
    if fix_fails:
        queue.assert_not_awaited()
        assert "### Suggested fix" not in rendered[0]
    else:
        queue.assert_awaited_once_with(run, stages=["fix_comment"])
        await post_investigation.process_stage(workflow(), 13, "fix_comment")
        assert order == ["comment", "fix", "comment"]
        if not github_fails:
            assert len(rendered) == 2 and rendered[0].splitlines()[0] == rendered[1].splitlines()[0]
            assert "### Suggested fix" not in rendered[0]
            assert "### Suggested fix" in rendered[1] and "untested and unverified" in rendered[1]
            assert "return a - b" not in rendered[1]
    assert investigation["status"] == "completed"
    token.assert_awaited_with(13, repository_id=10)

    install.status = "suspended"
    token.reset_mock()
    assert await post_investigation.process_stage(workflow(), 13, "diagnosis_comment") == (
        None, "installation_unavailable",
    )
    token.assert_not_awaited()

    investigation["diagnosis"]["evidence_validated"] = False
    assert await post_investigation.process_stage(workflow(), 13, "diagnosis_comment") == (
        None, "diagnosis_unvalidated",
    )
