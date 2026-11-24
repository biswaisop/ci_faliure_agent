import base64
import json
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from app.agent.fix_suggestion_agent import run_fix_suggestion
from app.schema.failure_packet import FailurePacket
from app.schema.fix_suggestion import FixSuggestion, SuggestedFileChange
from app.services import fix_suggestion_service
from app.services.fix_suggestion_service import FixSuggestionService
from app.services.github_source_reader import (
    MAX_FILE_BYTES,
    MAX_SOURCE_BYTES,
    GitHubSourceReader,
)
from tests.test_pr_commenting import (
    CLASSIFICATION,
    CONTEXT,
    DIAGNOSIS,
    HEAD,
    REPO,
    workflow,
)

FILE = "src/calculator.py"
OLD = "return x - y"
SOURCE = f"def add(x, y):\n    {OLD}\n"
FAILURE = CONTEXT.model_copy(update={"failure_packets": [
    FailurePacket(job_id=1, job_name="tests", relevant_log=f'File "/home/runner/work/project/project/{FILE}", line 2'),
]})


def proposal(**fields):
    return FixSuggestion(
        status="suggested", summary="Fix addition", rationale="The operation should add the operands.",
        changes=[SuggestedFileChange(path=FILE, original_snippet=OLD, replacement_snippet="return x + y",
                                     explanation="Use addition.")],
        evidence_ids=["log:1", f"source:{FILE}"],
    ).model_copy(update=fields)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["valid", "missing", "repeated", "absent", "evidence", "invalid_path", "bypass", "empty"])
async def test_fix_graph_validates_edit_without_execution(case):
    readers = {
        "missing": {}, "repeated": {FILE: SOURCE * 2}, "absent": {FILE: "print('unrelated')"},
    }
    reader = Mock(read_files=AsyncMock(return_value=readers.get(case, {FILE: SOURCE})))
    draft = proposal()
    if case == "evidence":
        draft.evidence_ids.append("log:invented")
    if case == "invalid_path":
        draft.changes[0].path = "../calculator.py"
    if case == "bypass":
        draft.changes[0].replacement_snippet = "exit(0)"
    if case == "empty":
        draft = proposal(status="insufficient_evidence", changes=[])
    model = Mock(with_structured_output=Mock(return_value=Mock(ainvoke=AsyncMock(return_value=draft))))
    result = await run_fix_suggestion(
        diagnosis=DIAGNOSIS, classification=CLASSIFICATION, context=FAILURE, reader=reader, llm=model,
    )
    assert not result.verified
    reader.read_files.assert_awaited_once()
    sha, paths = reader.read_files.await_args.args
    assert sha == HEAD and FILE in paths
    assert result.status == ("suggested" if case == "valid" else "insufficient_evidence")
    assert bool(result.changes) == (case == "valid")
    if case == "missing":
        model.with_structured_output.assert_not_called()
    else:
        model.with_structured_output.assert_called_once_with(FixSuggestion)


@pytest.mark.asyncio
async def test_source_reads_are_bounded_and_exclude_symlinks_secrets_and_traversal():
    tree_sha, link_sha = "b" * 40, "c" * 40
    files = [f"src/{i}.py" for i in range(8)]
    content = b"# a\n" * (MAX_FILE_BYTES // 4 - 1)
    blobs = {file: f"{i + 10:040x}" for i, file in enumerate(files)}
    entries = [
        {"path": file, "sha": blobs[file], "size": len(content), "type": "blob", "mode": "100644"}
        for file in files
    ]
    entries[0]["size"] = MAX_FILE_BYTES + 1
    entries.append({"path": "link.py", "sha": link_sha, "size": 10, "type": "blob", "mode": "120000"})

    async def request(path, token, **kwargs):
        assert token == "repository-scoped"
        assert path.startswith("/repos/sample/project/git/")
        if path.endswith(f"/commits/{HEAD}"):
            return json.dumps({"sha": HEAD, "tree": {"sha": tree_sha}}).encode()
        if path.endswith(f"/trees/{tree_sha}"):
            return json.dumps({"tree": entries}).encode()
        sha = path.rsplit("/", 1)[-1]
        assert sha not in {link_sha, blobs[files[0]]}
        return json.dumps({"encoding": "base64", "sha": sha, "content": base64.b64encode(content).decode()}).encode()

    collector = Mock(read_repository_response=AsyncMock(side_effect=request))
    reader = GitHubSourceReader(collector, REPO, "repository-scoped")
    result = await reader.read_files(HEAD, ["../x.py", "/a.py", ".env", "secrets.json", "link.py", *files])
    assert len(result) == MAX_SOURCE_BYTES // len(content)
    assert collector.read_repository_response.await_count == 2 + len(result)
    assert await reader.read_files("main", [FILE]) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [False, True])
async def test_fix_service_persists_failure_without_touching_diagnosis(monkeypatch, invalid):
    repo = Mock(get=AsyncMock(return_value=None), claim=AsyncMock(return_value={"_id": "lease"}), finish=AsyncMock())
    monkeypatch.setattr(fix_suggestion_service, "get_llm", Mock(return_value=object()))
    error = RuntimeError("model unavailable")
    if invalid:
        with pytest.raises(ValidationError) as caught:
            FixSuggestion.model_validate({"verified": True})
        error = caught.value
    monkeypatch.setattr(fix_suggestion_service, "run_fix_suggestion", AsyncMock(side_effect=error))
    result, retry = await FixSuggestionService(repo).generate(workflow(), FAILURE, CLASSIFICATION, DIAGNOSIS, Mock())
    assert result is None
    assert retry == (None if invalid else 60)
    assert repo.finish.await_args.kwargs["status"] == "failed"


@pytest.mark.asyncio
async def test_same_diagnosis_reuses_stored_fix():
    stored = proposal()
    repo = Mock(get=AsyncMock(return_value={"suggestion": stored.model_dump()}), claim=AsyncMock())
    assert await FixSuggestionService(repo).generate(workflow(), FAILURE, CLASSIFICATION, DIAGNOSIS, Mock()) == (
        stored, None,
    )
    repo.claim.assert_not_called()
