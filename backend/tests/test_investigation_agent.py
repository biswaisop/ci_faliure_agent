
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


class FakeStructuredLLM:
    def __init__(self, cited_ids):
        self.cited_ids = cited_ids

    async def ainvoke(self, messages):
        assert "One or two concise sentences" in messages[0][1]
        return DiagnosisDraft(
            summary="Python assertion failed",
            root_cause="A test assertion did not pass",
            confidence="high",
            evidence_ids=self.cited_ids,
            regression_attribution="supported",
            limitations=["Source available only as a patch."],
            suggested_actions=[
                "Inspect the failed assertion"
            ],
        )


class FakeLLM:
    def __init__(self, cited_ids):
        self.cited_ids = cited_ids

    def with_structured_output(self, schema):
        assert schema is DiagnosisDraft
        return FakeStructuredLLM(self.cited_ids)


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
        llm=FakeLLM(citations),
    )

    assert result.summary == "Python assertion failed"
    assert result.evidence_ids == citations
    assert result.evidence_validated is True
    assert (result.confidence, result.regression_attribution) == ("high", attribution)


def test_legacy_diagnosis_keeps_confidence_and_defaults_attribution():
    diagnosis = DiagnosisResult.model_validate({"summary": "Failed", "root_cause": "Type mismatch", "confidence": "high"})
    assert (diagnosis.confidence, diagnosis.regression_attribution, diagnosis.limitations) == ("high", "insufficient_evidence", [])
