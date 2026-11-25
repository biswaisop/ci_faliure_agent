import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import Field, PrivateAttr

from app.agent.investigation_agent import execute_investigation, run_investigation
from app.agent.tools.investigation import InvestigationTools
from app.db.repositories.investigation_repository import InvestigationRepository
from app.schema.diagnosis import DiagnosisResult
from app.schema.investigation import (
    ContextSelection,
    InvestigationLimits,
    RoutingDecision,
    SpecialistFinding,
)
from app.schema.investigation_context import (
    ChangedFile,
    FileCorrelation,
    InvestigationContext,
)
from app.services.failure_classifier import classify_failure
from app.services.investigation_service import InvestigationService
from app.services.log_reducer import reduce_job_log

pytestmark = pytest.mark.anyio

PAGES_ERROR = "TypeError: error must be an instance of Error"
PAGES_WARNING = "Node.js 20 is deprecated. Certain actions are being forced to run on Node.js 24."
PAGES_LOG = (
    "##[group]Run actions/configure-pages@v5\n"
    "with:\n  static_site_generator: next\n  enablement: false\n##[endgroup]\n"
    f"Injecting property=output and value=export in:\n##[error]{PAGES_ERROR}\n"
    f"##[warning]{PAGES_WARNING}"
)


class FakeLLM(BaseChatModel):
    model: str = "test-model"
    prompts: list = Field(default_factory=list)
    transcripts: list = Field(default_factory=list)
    _responses = PrivateAttr()
    _before_reply = PrivateAttr(default=None)

    def __init__(self, *responses):
        super().__init__()
        self._responses = iter(responses)

    @property
    def _llm_type(self):
        return "mock-nim"

    def bind_tools(self, tools, **kwargs):
        return self.bind(tools=tools)

    def _generate(self, *args, **kwargs):
        raise AssertionError("Agents must use async model calls")

    async def _agenerate(self, messages, **kwargs):
        if self._before_reply:
            await self._before_reply(messages)
        self.prompts.append((messages[0].content, json.loads(messages[1].content)))
        self.transcripts.append([json.loads(item.content) for item in messages if isinstance(item, ToolMessage)])
        reply = self.next_response()
        if isinstance(reply, list):
            actions = reply
        else:
            actions = [(type(reply).__name__, reply.model_dump(mode="json"))]
        message = AIMessage(content="", tool_calls=[
            {"name": name, "args": args, "id": uuid4().hex} for name, args in actions
        ])
        return ChatResult(generations=[ChatGeneration(message=message)])

    def with_structured_output(self, schema):
        assert schema is RoutingDecision
        return RunnableLambda(self.invoke_routing)

    async def invoke_routing(self, messages):
        self.prompts.append((messages[0][1], json.loads(messages[1][1])))
        return self.next_response()

    def next_response(self):
        reply = next(self._responses)
        if isinstance(reply, Exception):
            raise reply
        return reply


def make_context(*packets, **updates):
    return InvestigationContext(
        github_repo_id=100, run_id=200, run_attempt=1, workflow_id=300,
        head_sha="a" * 40, failure_packets=list(packets), **updates,
    )


def finding(specialist, packet, **updates):
    values = {"observed_failure": packet.error_message, "evidence_ids": [f"log:{packet.job_id}"],
              "confidence": "insufficient_evidence", "suspected_file": None, **updates}
    return SpecialistFinding(specialist=specialist, **values)


@pytest.mark.parametrize(("log", "step", "runtime", "origin", "specialist"), [
    ('Traceback (most recent call last):\n  File "src/main.py", line 4\nValueError: invalid',
     "Run program", "python", "application_code", "python"),
    ("E   AssertionError: expected 42\nFAILED tests/test_app.py::test_run - AssertionError",
     "Run pytest", "python", "test_execution", "python"),
    ("ERROR: Could not find a version that satisfies the requirement missing",
     "pip install -r requirements.txt", "python", "dependency_installation", "python"),
    ("× No solution found when resolving dependencies:\n##[error]Process completed with exit code 1.",
     "uv sync", "python", "dependency_installation", "python"),
    ("TypeError: value is undefined\n    at f (src/page.js:42:1)",
     "Run script", "javascript", "application_code", "javascript"),
    ("Error [ERR_MODULE_NOT_FOUND]: Cannot find package\n    at resolve (node:internal/modules/esm/resolve:15)",
     "Run script", "nodejs", "application_code", "javascript"),
    ("##[group]Run node app.cjs\nError: Cannot find module 'demo'\n    at Module._resolveFilename (node:internal/modules/cjs/loader:15)",
     "Run script", "nodejs", "application_code", "javascript"),
    ("##[group]Run npm run build\nNext.js 15\nFailed to compile.\nModule not found: Can't resolve 'demo'",
     "Build Next.js", "nextjs", "build_tool", "javascript"),
    ('Error occurred prerendering page "/profile"\nExport encountered errors',
     "Build", "nextjs", "build_tool", "javascript"),
    ("npm error code ERESOLVE\nnpm error unable to resolve dependency tree",
     "npm ci", "nodejs", "dependency_installation", "javascript"),
    ("src/page.tsx(42,1): error TS2322: Type mismatch", "Build", "typescript", "build_tool", "javascript"),
    ("##[group]Run vitest\nFAIL src/page.test.ts\nAssertionError: expected false",
     "Run tests", "javascript", "test_execution", "javascript"),
    (PAGES_LOG, "Setup Pages", "nodejs", "third_party_action", "github_actions"),
    ("TypeError: invalid value\n    at run (/home/runner/_work/_actions/example/setup/v1/dist/index.js:1:9)",
     "Custom setup", "nodejs", "third_party_action", "github_actions"),
    ("##[error]Invalid workflow file", "Initialize", "github_action", "github_platform", "github_actions"),
    ("##[error]No space left on device", "Run tests", "runner", "runner_environment", "github_actions"),
])
async def test_single_specialist(log, step, runtime, origin, specialist):
    packet = reduce_job_log(log, 123, "CI", step)
    assert (packet.runtime, packet.failure_origin) == (runtime, origin)
    assert packet.error_message and packet.error_message in log
    context = make_context(packet, comparison_status="available")
    model = FakeLLM(finding(specialist, packet))
    result = await execute_investigation(context=context, classification=classify_failure(context), llm=model)
    assert result.consulted == [specialist]
    assert result.usage.model_turns == 1 and result.usage.tool_calls == 0
    assert result.diagnosis.summary == packet.error_signature
    assert result.findings[0].confidence == "insufficient_evidence"
    assert result.diagnosis.evidence_ids == ["log:123"] and result.diagnosis.evidence_validated


async def test_pages_warning_is_not_a_cause_or_application_build():
    packet = reduce_job_log(PAGES_LOG, 123, "Deploy Next.js site to Pages", "Setup Pages")
    assert packet.action == "actions/configure-pages@v5"
    assert PAGES_WARNING in packet.warnings[0]
    context = make_context(packet)
    model = FakeLLM(finding(
        "github_actions", packet, confidence="high", likely_root_cause="The Next.js build failed on Node.js 24.",
        cause_quote=f"##[warning]{PAGES_WARNING}", cause_evidence_id="log:123",
    ))
    result = await run_investigation(context=context, classification=classify_failure(context), llm=model)
    assert result.summary == PAGES_ERROR and result.possible_cause is None
    assert result.evidence_ids == ["log:123"]
    assert any("warnings are leads" in limitation for limitation in result.limitations)
    assert model.prompts[0][1]["failure"]["action"] == packet.action


async def test_mixed_workflow_passes_all_job_logs_in_one_model_call():
    python = reduce_job_log("AssertionError: mismatch", 1, "pytest", "pytest")
    javascript = reduce_job_log("src/main.ts(1,1): error TS2322: Type mismatch", 2, "Build")
    actions = reduce_job_log(PAGES_LOG, 3, "Deploy", "Setup Pages")
    context = make_context(python, javascript, actions)
    model = FakeLLM(finding("github_actions", actions))
    result = await execute_investigation(context=context, classification=classify_failure(context), llm=model)
    assert result.consulted == ["github_actions"]
    assert len(model.prompts) == 1
    assert result.diagnosis.summary == PAGES_ERROR
    assert set(model.prompts[0][1]["evidence"]) == {"log:1", "log:2", "log:3"}


@pytest.mark.parametrize("log", ["##[warning]Node.js 20 is deprecated.", ""])
async def test_ambiguous_or_unknown_fails_closed(log):
    packet = reduce_job_log(log, 123, "CI", "Check")
    context = make_context(
        packet, changed_files=[ChangedFile(filename="app.py", status="modified"),
                               ChangedFile(filename="app.ts", status="modified")],
    )
    model = FakeLLM()
    result = await execute_investigation(context=context, classification=classify_failure(context), llm=model)
    assert not result.consulted and not model.prompts
    assert result.status == "partial" and result.diagnosis.possible_cause is None


async def test_optional_router_makes_one_bounded_decision():
    packet = reduce_job_log("TypeError: invalid value", 123, "CI", "Check")
    context = make_context(packet)
    tools = InvestigationTools(context, InvestigationLimits(llm_routing=True))
    llm = FakeLLM({"specialist": "unexpected"})
    result = await execute_investigation(context=context, classification=classify_failure(context), llm=llm, tools=tools)
    assert result.usage.model_turns == result.usage.routing_turns == 1
    assert result.consulted == []
    assert result.diagnosis.summary == packet.error_signature


async def test_optional_router_requires_distinct_routing_evidence():
    packet = reduce_job_log("Launching ECMAScript loader\nTypeError: invalid value", 123, "CI", "Check")
    context = make_context(packet)
    tools = InvestigationTools(context, InvestigationLimits(llm_routing=True))
    model = FakeLLM(
        RoutingDecision(specialist="javascript", evidence_quote="Launching ECMAScript loader"),
        finding("javascript", packet),
    )
    result = await execute_investigation(context=context, classification=classify_failure(context), llm=model, tools=tools)
    assert len(model.prompts) == result.usage.model_turns == 2
    assert result.usage.routing_turns == 1 and result.consulted == ["javascript"]


@pytest.mark.parametrize(("cause", "supported"), [
    ("The hard-coded value in app.py may explain the assertion mismatch.", True),
    ("The failure comes from imaginary.py.", False),
    ("The cause is at line 17 in app.py.", False),
    ("We verified the fix.", False),
])
async def test_validator_checks_log_explanations_without_claiming_regression(cause, supported):
    packet = reduce_job_log("value = 'different'\nAssertionError: mismatch", 123, "pytest")
    context = make_context(
        packet, comparison_status="available", diff_available=True,
        changed_files=[ChangedFile(filename="app.py", status="modified", patch="+value = 'different'")],
        correlations=[FileCorrelation(failing_file="app.py", changed_file="app.py", match_type="direct")],
    )
    model = FakeLLM(
        finding(
            "python", packet, confidence="medium", likely_root_cause=cause, suspected_file="app.py",
            cause_evidence_id="log:123", cause_quote="value = 'different'",
        ),
    )
    result = await run_investigation(context=context, classification=classify_failure(context), llm=model)
    assert result.regression_attribution == "insufficient_evidence"
    assert result.possible_cause == (cause if supported else None)


async def test_invalid_citation_is_discarded():
    packet = reduce_job_log("AssertionError: mismatch", 123, "pytest")
    context = make_context(packet)
    model = FakeLLM(finding("python", packet, evidence_ids=["log:123", "source:invented.py"]))
    result = await run_investigation(context=context, classification=classify_failure(context), llm=model)
    assert not result.evidence_validated
    assert result.evidence_ids == ["log:123"] and result.unsupported_evidence_ids == ["source:invented.py"]
    assert result.summary == packet.error_signature


async def test_model_api_failure_keeps_observed_error():
    packet = reduce_job_log("AssertionError: mismatch", 123, "pytest")
    context = make_context(packet)
    model = FakeLLM(RuntimeError("NIM unavailable"))
    result = await execute_investigation(context=context, classification=classify_failure(context), llm=model)
    assert (result.usage.model_turns, result.usage.tool_calls) == (1, 0)
    assert result.status == "partial" and result.diagnosis.summary == packet.error_signature
    assert len(result.findings) == 0
    assert any("model response" in item for item in result.diagnosis.limitations)


async def test_investigator_rejects_a_filename_outside_the_list():
    packet = reduce_job_log("TypeError: value is undefined", 123, "CI", "Check")
    context = make_context(packet, changed_files=[ChangedFile(filename="app.ts", status="modified")])
    model = FakeLLM(finding("github_actions", packet, suspected_file="invented.ts"))
    result = await execute_investigation(context=context, classification=classify_failure(context), llm=model)
    assert result.usage.model_turns == 1 and result.usage.tool_calls == 0
    assert model.prompts[0][1]["changed_files"] == ["app.ts"]
    assert result.diagnosis.suspected_file is None
    assert result.status == "partial" and result.diagnosis.summary == packet.error_signature


async def test_missing_python_module_has_a_cited_report_without_a_model_finding():
    packet = reduce_job_log(
        "##[group]Run pytest --junitxml=reports/test-results.xml\n"
        "src/calculator.py:5: in <module>\n"
        "    import requests\n"
        "E   ModuleNotFoundError: No module named 'requests'",
        123, "Run Python Test Suite", "Run tests",
    )
    context = make_context(packet, comparison_status="no_previous_success")
    classification = classify_failure(context)
    assert "ModuleNotFoundError" in " ".join(classification.reasons)
    model = FakeLLM(RuntimeError("model unavailable"))
    result = await execute_investigation(context=context, classification=classification, llm=model)
    assert result.consulted == ["python"]
    assert result.diagnosis.summary == "ModuleNotFoundError: No module named 'requests'"
    assert result.diagnosis.root_cause == "Python could not import `requests` in the CI environment."
    assert result.diagnosis.evidence_validated and result.diagnosis.evidence_ids == ["log:123"]
    assert result.diagnosis.regression_attribution == "insufficient_evidence"


async def test_service_persists_diagnosis_metadata_and_legacy_api_shape():
    packet = reduce_job_log("AssertionError: mismatch", 123, "pytest")
    context = make_context(
        packet, comparison_status="available",
        changed_files=[ChangedFile(filename="app.py", status="modified", patch="+return different")],
    )
    collection = SimpleNamespace(update_one=AsyncMock(return_value=SimpleNamespace(matched_count=1)))
    repository = InvestigationRepository(collection)
    model = FakeLLM(ContextSelection(relevant_diffs=["diff:app.py"]), finding("python", packet))
    result = await InvestigationService(repository, model).investigate(
        context=context, classification=classify_failure(context),
    )
    key, update = collection.update_one.await_args.args
    stored = update["$set"]
    assert key == {"github_repo_id": 100, "run_id": 200, "run_attempt": 1}
    assert stored["status"] == stored["execution_status"] == "completed"
    assert stored["investigation_metadata"]["selected_specialist"] == "python"
    assert stored["investigation_metadata"]["model"] == "test-model"
    assert stored["investigation_metadata"]["relevant_diffs"] == ["diff:app.py"]
    assert stored["investigation_metadata"]["usage"]["context_model_turns"] == 1
    assert DiagnosisResult.model_validate(stored["diagnosis"]) == result
    assert DiagnosisResult.model_validate({"summary": "Old", "root_cause": "Unknown", "confidence": "high"}).limitations == []
