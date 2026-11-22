
from app.schema.failure_packet import FailurePacket
from app.services.investigation_context_builder import (
    build_investigation_context,
)


def test_correlates_renamed_file():
    packet = FailurePacket(
        job_id=123,
        job_name="pytest",
        failing_tests=[
            "tests/test_auth.py::test_login"
        ],
        relevant_log="ImportError: missing User",
    )

    context = build_investigation_context(
        github_repo_id=100,
        run_id=200,
        run_attempt=1,
        workflow_id=300,
        head_sha="abc123",
        head_branch="main",
        base_sha="def456",
        failure_packets=[packet],
        diff={
            "files": [
                {
                    "filename": "tests/test_authentication.py",
                    "previous_filename": "tests/test_auth.py",
                    "status": "renamed",
                    "additions": 5,
                    "deletions": 2,
                    "patch": "@@ ...",
                }
            ]
        },
    )

    assert len(context.correlations) == 1
    assert context.correlations[0].match_type == "renamed"
    assert context.diff_available is True


def test_no_baseline_run():
    context = build_investigation_context(
        github_repo_id=100,
        run_id=200,
        run_attempt=1,
        workflow_id=300,
        head_sha="abc123",
        head_branch="main",
        failure_packets=[],
    )

    assert context.base_sha is None
    assert context.diff_available is False
    assert context.correlations == []
