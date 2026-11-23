
import pytest

from app.agent.investigation_agent import (
    run_investigation,
)
from app.schema.diagnosis import DiagnosisDraft
from app.schema.failure_classification import (
    FailureClassification,
)
from app.schema.failure_packet import FailurePacket
from app.schema.investigation_context import (
    InvestigationContext,
)


class FakeStructuredLLM:
    async def ainvoke(self, messages):
        return DiagnosisDraft(
            summary="Python assertion failed",
            root_cause="A test assertion did not pass",
            confidence="medium",
            evidence_ids=["log:123"],
            suggested_actions=[
                "Inspect the failed assertion"
            ],
        )


class FakeLLM:
    def with_structured_output(self, schema):
        assert schema is DiagnosisDraft
        return FakeStructuredLLM()


@pytest.mark.asyncio
async def test_investigation_graph():

    context = InvestigationContext(
        github_repo_id=100,
        run_id=200,
        run_attempt=1,
        workflow_id=300,
        head_sha="abc123",
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

    result = await run_investigation(
        context=context,
        classification=classification,
        llm=FakeLLM(),
    )

    assert result.summary == "Python assertion failed"
    assert result.evidence_ids == ["log:123"]
    assert result.evidence_validated is True
