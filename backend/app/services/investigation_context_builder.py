
import re
from pathlib import PurePosixPath

from app.schema.failure_packet import FailurePacket
from app.schema.investigation_context import (
    ChangedFile,
    FileCorrelation,
    InvestigationContext,
)


# Recognizes common pytest file paths in log excerpts.
PYTHON_PATH_RE = re.compile(
    r"(?<![\w/.-])"
    r"((?:[\w.-]+/)*[\w.-]+\.py)"
    r"(?::\d+)?"
)


def normalize_path(path: str) -> str:
    """Normalize GitHub repository paths for comparison."""
    return str(
        PurePosixPath(
            path.replace("\\", "/").removeprefix("./")
        )
    )


def extract_failing_files(
    packets: list[FailurePacket],
) -> set[str]:
    """Find Python file references in failure packets."""

    files: set[str] = set()

    for packet in packets:
        for test in packet.failing_tests:
            path = test.split("::", 1)[0]
            if path.endswith(".py"):
                files.add(normalize_path(path))

        for match in PYTHON_PATH_RE.finditer(
            packet.relevant_log
        ):
            files.add(normalize_path(match.group(1)))

    return files


def correlate_files(
    packets: list[FailurePacket],
    changed_files: list[ChangedFile],
) -> list[FileCorrelation]:
    """Match failing file paths against changed files."""

    failing_files = extract_failing_files(packets)
    correlations = []

    for failing_file in sorted(failing_files):
        for changed in changed_files:

            current_path = normalize_path(
                changed.filename
            )

            previous_path = (
                normalize_path(changed.previous_filename)
                if changed.previous_filename
                else None
            )

            if failing_file == current_path:
                match_type = "direct"

            elif (
                previous_path
                and failing_file == previous_path
            ):
                match_type = "renamed"

            else:
                continue

            correlations.append(
                FileCorrelation(
                    failing_file=failing_file,
                    changed_file=changed.filename,
                    match_type=match_type,
                )
            )

    return correlations


def build_investigation_context(
    *,
    github_repo_id: int,
    run_id: int,
    run_attempt: int,
    workflow_id: int,
    head_sha: str,
    head_branch: str | None,
    failure_packets: list[FailurePacket],
    base_sha: str | None = None,
    diff: dict | None = None,
) -> InvestigationContext:
    """Assemble collected CI evidence into an investigation context."""

    changed_files = [
        ChangedFile.model_validate(file)
        for file in (diff or {}).get("files", [])
    ]

    correlations = correlate_files(
        failure_packets,
        changed_files,
    )

    return InvestigationContext(
        github_repo_id=github_repo_id,
        run_id=run_id,
        run_attempt=run_attempt,
        workflow_id=workflow_id,
        head_sha=head_sha,
        head_branch=head_branch,
        base_sha=base_sha,
        failure_packets=failure_packets,
        changed_files=changed_files,
        correlations=correlations,
        diff_available=diff is not None,
        diff_may_be_incomplete=(
            len(changed_files) >= 300
            or bool(
                (diff or {}).get("files_limit_reached")
            )
        ),
    )
