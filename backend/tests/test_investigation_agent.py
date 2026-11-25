import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent.investigation_agent import execute_investigation, run_investigation
from app.agent.investigation_tools import InvestigationTools
from app.db.repositories.investigation_repository import InvestigationRepository
from app.schema.diagnosis import DiagnosisResult
from app.schema.investigation import (
    InvestigationLimits,
    RoutingDecision,
    SpecialistFinding,
    SpecialistTurn,
    ToolRequest,
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


class FakeLLM:
    model = "test-model"

    def __init__(self, *responses):
        self.responses = iter(responses)
        self.prompts = []

    def with_structured_output(self, schema):
        assert schema in {SpecialistTurn, RoutingDecision}
        return self

    async def ainvoke(self, messages):
        self.prompts.append((messages[0][1], json.loads(messages[1][1])))
        reply = next(self.responses)
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
              "confidence": "insufficient_evidence", **updates}
    return SpecialistTurn(finding=SpecialistFinding(specialist=specialist, **values))


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


@pytest.mark.parametrize("limit", [1, 2])
async def test_mixed_workflow_consults_only_with_evidence_and_budget(limit):
    python = reduce_job_log("AssertionError: mismatch", 1, "pytest", "pytest")
    javascript = reduce_job_log("src/main.ts(1,1): error TS2322: Type mismatch", 2, "Build")
    actions = reduce_job_log(PAGES_LOG, 3, "Deploy", "Setup Pages")
    context = make_context(python, javascript, actions)
    tools = InvestigationTools(context, InvestigationLimits(consultations=limit))
    model = FakeLLM(
        finding("github_actions", actions, needs_second_opinion=True, suggested_specialist="javascript"),
        finding("javascript", javascript, needs_second_opinion=True, suggested_specialist="python"),
    )
    result = await execute_investigation(context=context, classification=classify_failure(context), llm=model, tools=tools)
    assert result.consulted == ["github_actions", "javascript"][:limit]
    assert len(model.prompts) == limit
    assert result.diagnosis.summary == PAGES_ERROR
    assert any("consultation budget" in item for item in result.diagnosis.limitations)
    if limit == 2:
        assert model.prompts[1][1]["previous_findings"][0]["specialist"] == "github_actions"


@pytest.mark.parametrize("log", ["TypeError: value is undefined", "##[warning]Node.js 20 is deprecated.", ""])
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


@pytest.mark.parametrize(("cause", "attribution"), [
    ("The hard-coded value in app.py may explain the assertion mismatch.", "possible"),
    ("The failure comes from imaginary.py.", "insufficient_evidence"),
    ("The cause is at line 17 in app.py.", "insufficient_evidence"),
    ("We verified the fix.", "insufficient_evidence"),
])
async def test_validator_checks_references_and_keeps_attribution_cautious(cause, attribution):
    packet = reduce_job_log("AssertionError: mismatch", 123, "pytest")
    context = make_context(
        packet, comparison_status="available", diff_available=True,
        changed_files=[ChangedFile(filename="app.py", status="modified", patch="+value = 'different'")],
        correlations=[FileCorrelation(failing_file="app.py", changed_file="app.py", match_type="direct")],
    )
    model = FakeLLM(finding(
        "python", packet, confidence="medium", likely_root_cause=cause, evidence_ids=["log:123", "diff:app.py"],
        cause_evidence_id="diff:app.py", cause_quote="value = 'different'",
    ))
    result = await run_investigation(context=context, classification=classify_failure(context), llm=model)
    assert result.regression_attribution == attribution
    assert result.possible_cause == (cause if attribution == "possible" else None)


async def test_invalid_citation_is_discarded():
    packet = reduce_job_log("AssertionError: mismatch", 123, "pytest")
    context = make_context(packet)
    model = FakeLLM(finding("python", packet, evidence_ids=["log:123", "source:invented.py"]))
    result = await run_investigation(context=context, classification=classify_failure(context), llm=model)
    assert not result.evidence_validated
    assert result.evidence_ids == ["log:123"] and result.unsupported_evidence_ids == ["source:invented.py"]
    assert result.summary == packet.error_signature


async def test_budget_exhaustion_and_model_api_failure_keep_observed_error():
    packet = reduce_job_log("AssertionError: mismatch", 123, "pytest")
    context = make_context(packet)
    request = ToolRequest(name="get_failed_job_details", job_id=123)
    model = FakeLLM(SpecialistTurn(tool_requests=[request, request]), RuntimeError("NIM unavailable"))
    tools = InvestigationTools(context, InvestigationLimits(specialist_turns=2, tool_calls=1))
    result = await execute_investigation(context=context, classification=classify_failure(context), llm=model, tools=tools)
    assert (result.usage.model_turns, result.usage.tool_calls) == (2, 1)
    assert result.status == "partial" and result.diagnosis.summary == packet.error_signature
    assert len(result.findings) == 0
    assert any("tool-call budget" in item for item in result.diagnosis.limitations)
    assert any("model response" in item for item in result.diagnosis.limitations)


async def test_specialist_stops_after_four_model_turns():
    packet = reduce_job_log("AssertionError: mismatch", 123, "pytest")
    context = make_context(packet)
    reply = SpecialistTurn(tool_requests=[ToolRequest(name="get_failed_job_details", job_id=123)])
    model = FakeLLM(*([reply] * 4))
    result = await execute_investigation(context=context, classification=classify_failure(context), llm=model)
    assert result.usage.model_turns == 4 and result.usage.tool_calls == 3
    assert result.status == "partial" and result.diagnosis.summary == packet.error_signature


async def test_service_persists_diagnosis_metadata_and_legacy_api_shape():
    packet = reduce_job_log("AssertionError: mismatch", 123, "pytest")
    context = make_context(packet, comparison_status="available")
    collection = SimpleNamespace(update_one=AsyncMock(return_value=SimpleNamespace(matched_count=1)))
    repository = InvestigationRepository(collection)
    result = await InvestigationService(repository, FakeLLM(finding("python", packet))).investigate(
        context=context, classification=classify_failure(context),
    )
    key, update = collection.update_one.await_args.args
    stored = update["$set"]
    assert key == {"github_repo_id": 100, "run_id": 200, "run_attempt": 1}
    assert stored["status"] == stored["execution_status"] == "completed"
    assert stored["investigation_metadata"]["selected_specialist"] == "python"
    assert stored["investigation_metadata"]["model"] == "test-model"
    assert DiagnosisResult.model_validate(stored["diagnosis"]) == result
    assert DiagnosisResult.model_validate({"summary": "Old", "root_cause": "Unknown", "confidence": "high"}).limitations == []
