
import re

from app.schema.failure_packet import FailurePacket

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
GITHUB_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T"
    r"\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\s+"
)

PYTEST_FAILURE_RE = re.compile(
    r"^FAILED\s+(\S+?::\S+)"
    r"(?:\s+-\s+.*)?$"
)

ERROR_RE = re.compile(
    r"(?P<type>^Error|[A-Za-z_]\w*(?:Error|Exception))"
    r":\s*(?P<message>.+)"
)


def clean_log(raw_log: str) -> list[str]:
    """Remove ANSI codes and GitHub runner timestamps."""
    cleaned = []

    for line in raw_log.splitlines():
        line = ANSI_RE.sub("", line)
        line = GITHUB_TIMESTAMP_RE.sub("", line)
        cleaned.append(line.rstrip())

    return cleaned


def extract_failing_tests(lines: list[str]) -> list[str]:
    """Extract pytest test identifiers from failure summaries."""
    tests = []

    for line in lines:
        match = PYTEST_FAILURE_RE.match(line.strip())

        if match:
            test_name = match.group(1)

            if test_name not in tests:
                tests.append(test_name)

    return tests


def extract_error(
    lines: list[str],
) -> tuple[str | None, str | None, int | None]:
    """Return the last recognizable exception or standalone error line."""
    result = (None, None, None)

    for index, line in enumerate(lines):
        match = ERROR_RE.search(line.lstrip())

        if match:
            result = (
                match.group("type"),
                match.group("message").strip(),
                index,
            )

    return result


def extract_relevant_log(
    lines: list[str],
    error_index: int | None,
    context_before: int = 25,
    context_after: int = 10,
    max_lines: int = 150,
) -> str:
    """Extract a bounded log slice around the detected error."""

    if not lines:
        return ""

    if error_index is None:
        return "\n".join(lines[-max_lines:])

    start = max(0, error_index - context_before)
    end = min(len(lines), error_index + context_after + 1)

    return "\n".join(lines[start:end][-max_lines:])


def reduce_job_log(
    raw_log: str | None,
    job_id: int,
    job_name: str,
    failed_step: str | None = None,
    exit_code: int | None = None,
) -> FailurePacket:
    """
    Convert GitHub Actions job logs into a compact
    failure packet for downstream investigation.
    """

    if raw_log is None:
        return FailurePacket(
            job_id=job_id,
            job_name=job_name,
            failed_step=failed_step,
            exit_code=exit_code,
            extraction_status="log_unavailable",
        )

    lines = clean_log(raw_log)

    failing_tests = extract_failing_tests(lines)

    error_type, error_message, error_index = extract_error(lines)

    relevant_log = extract_relevant_log(
        lines,
        error_index,
    )

    signature = None

    if error_type:
        signature = f"{error_type}: {error_message}"

    return FailurePacket(
        job_id=job_id,
        job_name=job_name,
        failed_step=failed_step,
        error_type=error_type,
        error_message=error_message,
        error_signature=signature,
        failing_tests=failing_tests,
        relevant_log=relevant_log,
        exit_code=exit_code,
        extraction_status=(
            "extracted"
            if error_type or failing_tests
            else "no_failure_found"
        ),
    )
