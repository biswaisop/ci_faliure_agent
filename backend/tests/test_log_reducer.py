
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
