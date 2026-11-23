
from app.schema.failure_packet import FailurePacket
from app.schema.investigation_context import InvestigationContext
from app.services.failure_classifier import classify_failure


def make_context(
    error_type: str | None,
    error_message: str,
) -> InvestigationContext:
    packet = FailurePacket(
        job_id=123,
        job_name="pytest",
        error_type=error_type,
        error_message=error_message,
        relevant_log=error_message,
    )

    return InvestigationContext(
        github_repo_id=100,
        run_id=200,
        run_attempt=1,
        workflow_id=300,
        head_sha="abc123",
        failure_packets=[packet],
    )


def test_code_bug():
    context = make_context(
        "AssertionError",
        "assert 2 == 3",
    )

    result = classify_failure(context)

    assert result.category == "code_bug"


def test_dependency_failure():
    context = make_context(
        None,
        "ERROR: No matching distribution found for package",
    )

    result = classify_failure(context)

    assert result.category == "dependency"


def test_infrastructure_failure():
    context = make_context(
        None,
        "Could not resolve host: github.com",
    )

    result = classify_failure(context)

    assert result.category == "infra_config"


def test_unknown_failure():
    context = make_context(
        None,
        "Process exited unexpectedly",
    )

    result = classify_failure(context)

    assert result.category == "unknown"


def test_missing_evidence():
    context = InvestigationContext(
        github_repo_id=100,
        run_id=200,
        run_attempt=1,
        workflow_id=300,
        head_sha="abc123",
    )

    result = classify_failure(context)

    assert result.category == "unknown"
    assert result.confidence == 0.0
