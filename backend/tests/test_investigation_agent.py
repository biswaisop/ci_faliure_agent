
import pytest

from app.agent.investigation_agent import (
    run_investigation,
)
from app.schema.diagnosis import DiagnosisDraft, DiagnosisResult
from app.schema.failure_classification import (
    FailureClassification,
)
from app.schema.failure_packet import FailurePacket
from app.schema.investigation_context import (
    ChangedFile,
    FileCorrelation,
    InvestigationContext,
)
from app.services.log_reducer import reduce_job_log


class FakeStructuredLLM:
    def __init__(self, draft):
        self.draft = draft

    async def ainvoke(self, messages):
        return self.draft


class FakeLLM:
    def __init__(self, draft):
        self.draft = draft

    def with_structured_output(self, schema):
        assert schema is DiagnosisDraft
        return FakeStructuredLLM(self.draft)


@pytest.mark.asyncio
@pytest.mark.parametrize("has_diff, patch, correlated, attribution", [
    (False, "", False, "insufficient_evidence"),
    (True, "", True, "insufficient_evidence"),
    (True, "+new", False, "possible"),
    (True, "+new", True, "supported"),
])
async def test_investigation_graph(has_diff, patch, correlated, attribution):

    context = InvestigationContext(
        github_repo_id=100,
        run_id=200,
        run_attempt=1,
        workflow_id=300,
        head_sha="abc123",
        diff_available=has_diff,
        comparison_status="available" if has_diff else "no_previous_success",
        changed_files=[ChangedFile(filename="app.py", status="modified", patch=patch)] if has_diff else [],
        correlations=[FileCorrelation(failing_file="app.py", changed_file="app.py", match_type="direct")] if correlated else [],
        failure_packets=[
            FailurePacket(
                job_id=123,
                job_name="pytest",
                error_type="AssertionError",
                error_message="assert 2 == 3",
                relevant_log="AssertionError: assert 2 == 3",
            )
        ],
    )

    classification = FailureClassification(
        category="code_bug",
        confidence=0.8,
        reasons=["AssertionError detected"],
    )

    citations = ["log:123", "diff:app.py"] if has_diff else ["log:123"]
    result = await run_investigation(
        context=context,
        classification=classification,
        llm=FakeLLM(DiagnosisDraft(
            observed_error="AssertionError: assert 2 == 3",
            confidence="high",
            evidence_ids=citations,
            regression_attribution="supported",
        )),
    )

    assert result.summary == "AssertionError: assert 2 == 3"
    assert result.evidence_ids == citations
    assert result.evidence_validated is True
    assert (result.confidence, result.regression_attribution) == ("high", attribution)


@pytest.mark.asyncio
@pytest.mark.parametrize("quote_error", [True, False])
async def test_warning_stays_a_hypothesis_when_cited_log_also_contains_error(quote_error):
    error = "Error: Couldn't find issue info in current context"
    hypothesis = "The unsupported numOfAssignee input caused the missing issue context."
    packet = reduce_job_log(
        "2026-11-24T13:19:57.8339175Z ##[warning]Unexpected input(s) 'numOfAssignee'\n"
        f"2026-11-24T13:19:58.0776416Z {error}\n"
        "2026-11-24T13:19:58.1528278Z ##[warning]Node.js 20 is deprecated.",
        123, "Assign issue",
    )
    result = await run_investigation(
        context=InvestigationContext(
            github_repo_id=100, run_id=200, run_attempt=1, workflow_id=300, head_sha="abc123",
            failure_packets=[packet],
        ),
        classification=FailureClassification(category="code_bug", confidence=0.8, reasons=[]),
        llm=FakeLLM(DiagnosisDraft(
            observed_error=error if quote_error else hypothesis,
            possible_cause=hypothesis,
            confidence="high" if quote_error else "insufficient_evidence",
            evidence_ids=["log:123"],
        )),
    )

    assert result.evidence_validated
    assert result.possible_cause == hypothesis
    assert "could not be confirmed" in result.root_cause
    assert hypothesis not in result.summary
    assert result.confidence == "high"
    assert result.summary == error


@pytest.mark.asyncio
@pytest.mark.parametrize("reported_error", [None, "Pytest failed because of a dependency error."])
async def test_extracted_error_is_reported_when_model_omits_an_exact_quote(reported_error):
    error = "ModuleNotFoundError: No module named 'requests'"
    packet = reduce_job_log(
        "2026-11-24T13:15:11.1463007Z     import requests\n"
        f"2026-11-24T13:15:11.1465362Z E   {error}\n"
        "2026-11-24T13:15:11.1624874Z ##[error]Process completed with exit code 2.",
        123, "Run Python Test Suite",
    )
    result = await run_investigation(
        context=InvestigationContext(
            github_repo_id=100, run_id=200, run_attempt=1, workflow_id=300, head_sha="abc123",
            failure_packets=[packet],
        ),
        classification=FailureClassification(category="code_bug", confidence=0.8, reasons=[]),
        llm=FakeLLM(DiagnosisDraft(
            observed_error=reported_error,
            confidence="insufficient_evidence",
        )),
    )

    assert result.summary == error
    assert result.confidence == "high"
    assert result.evidence_ids == ["log:123"]
    assert result.evidence_validated
    assert "could not be confirmed" in result.root_cause


def test_legacy_diagnosis_keeps_confidence_and_defaults_attribution():
    diagnosis = DiagnosisResult.model_validate({"summary": "Failed", "root_cause": "Type mismatch", "confidence": "high"})
    assert (diagnosis.confidence, diagnosis.regression_attribution, diagnosis.limitations) == ("high", "insufficient_evidence", [])
    assert diagnosis.possible_cause is None
