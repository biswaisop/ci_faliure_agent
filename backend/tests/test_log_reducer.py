from datetime import UTC, datetime

from app.services.log_reducer import reduce_job_log


def test_pytest_failure_extraction():
    raw_log = """
2026-11-23T10:00:00Z Running pytest

tests/test_auth.py::test_login FAILED

E   ImportError: cannot import name 'User'

FAILED tests/test_auth.py::test_login - ImportError
"""

    packet = reduce_job_log(
        raw_log=raw_log,
        job_id=12345,
        job_name="pytest",
        failed_step="Run tests",
        exit_code=1,
    )

    assert packet.error_type == "ImportError"
    assert packet.error_message == "cannot import name 'User'"
    assert packet.failing_tests == [
        "tests/test_auth.py::test_login"
    ]
    assert packet.extraction_status == "extracted"
    assert packet.exit_code == 1


def test_missing_log():
    packet = reduce_job_log(
        raw_log=None,
        job_id=12345,
        job_name="pytest",
    )

    assert packet.extraction_status == "log_unavailable"
    assert packet.relevant_log == ""


def test_large_log_is_bounded():
    raw_log = (
        "Normal output\n" * 10000
        + "ValueError: invalid configuration\n"
    )

    packet = reduce_job_log(
        raw_log=raw_log,
        job_id=12345,
        job_name="pytest",
    )

    assert packet.error_type == "ValueError"
    assert len(packet.relevant_log.splitlines()) <= 150


def test_failed_step_timing_excludes_errors_from_other_steps():
    packet = reduce_job_log(
        "2026-11-24T10:00:01.000Z ##[group]Run actions/setup-node@v4\n"
        "2026-11-24T10:00:12.000Z ##[group]Run pytest\n"
        "2026-11-24T10:00:13.000Z AssertionError: expected true\n"
        "2026-11-24T10:00:30.000Z RuntimeError: cleanup failed\n",
        1, "Build", "Run integration tests",
        step_started_at=datetime(2026, 11, 24, 10, 0, 10, tzinfo=UTC),
        step_completed_at=datetime(2026, 11, 24, 10, 0, 15, tzinfo=UTC),
    )
    assert packet.error_signature == "AssertionError: expected true"
    assert packet.runtime == "python" and packet.action is None
    assert "cleanup" not in packet.relevant_log


def test_pytest_import_error_in_step_completion_second_is_retained():
    packet = reduce_job_log(
        "2026-11-25T18:08:07.9388563Z ##[group]Run pytest --junitxml=reports/test-results.xml\n"
        "2026-11-25T18:08:07.9425199Z ##[endgroup]\n"
        "2026-11-25T18:08:08.5094446Z collecting ... collected 0 items / 1 error\n"
        "2026-11-25T18:08:08.5263004Z src/calculator.py:5: in <module>\n"
        "2026-11-25T18:08:08.5282877Z     import requests\n"
        "2026-11-25T18:08:08.5283670Z E   ModuleNotFoundError: No module named 'requests'\n"
        "2026-11-25T18:08:08.5404557Z ##[error]Process completed with exit code 2.\n"
        "2026-11-25T18:08:08.5496159Z ##[group]Run actions/upload-artifact@v4\n"
        "2026-11-25T18:08:08.7294174Z RuntimeError: later step failed\n",
        1, "Run Python Test Suite", "Run tests",
        step_started_at=datetime(2026, 11, 25, 18, 8, 7, tzinfo=UTC),
        step_completed_at=datetime(2026, 11, 25, 18, 8, 8, tzinfo=UTC),
    )
    assert packet.error_signature == "ModuleNotFoundError: No module named 'requests'"
    assert packet.runtime == "python" and packet.action is None
    assert "src/calculator.py:5" in packet.relevant_log
    assert "later step failed" not in packet.relevant_log
