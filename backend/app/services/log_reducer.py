
import re
from datetime import datetime

from app.schema.failure_packet import FailureOrigin, FailurePacket, Runtime

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
GITHUB_TIMESTAMP_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T"
    r"\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)\s+"
)

PYTEST_FAILURE_RE = re.compile(
    r"^FAILED\s+(\S+?::\S+)"
    r"(?:\s+-\s+.*)?$"
)

ERROR_RE = re.compile(
    r"(?P<type>\bError|[A-Za-z_]\w*(?:Error|Exception))"
    r"(?:\s*\[(?P<code>[A-Z_0-9]+)\])?:\s*(?P<message>.+)"
)
ACTION = r"[\w.-]+/[\w./-]+@[\w./-]+"
ACTION_FRAME = re.compile(r"[/\\]_actions[/\\]([\w.-]+)[/\\]([\w.-]+)[/\\]([\w.-]+)[/\\]")
RUN_GROUP = re.compile(r"^##\[group\]Run (.+)")
WARNING = re.compile(r"^\s*(?:##\[warning\]|(?:npm )?warn(?:ing)?\b)|Node\.js \d+ is deprecated|forced to run on Node\.js", re.IGNORECASE)
NODE_WARNING = re.compile(r"Node\.js \d+ is deprecated|forced to run on Node\.js", re.IGNORECASE)
EXIT = re.compile(r"^##\[error\]Process completed with exit code", re.IGNORECASE)
RUNNER = re.compile(r"no space left on device|ENOSPC|runner.*(?:lost communication|shut down)|out of memory", re.IGNORECASE)
PLATFORM = re.compile(
    r"resource not accessible by integration|API rate limit exceeded|invalid workflow file|workflow is not valid|"
    r"unable to (?:resolve|download) action|github api.{0,80}\b(?:401|403|429)\b", re.IGNORECASE,
)
PY_DEPS = re.compile(r"resolutionimpossible|no matching distribution|could not find a version that satisfies|no solution found when resolving dependencies|failed to build wheel", re.IGNORECASE)
JS_DEPS = re.compile(
    r"\bERESOLVE\b|ERR_PNPM_(?:OUTDATED_LOCKFILE|NO_MATCHING_VERSION|PEER_DEP_ISSUES|FETCH_\w+)|"
    r"YN\d{4}:.*(?:error|failed)|(?:npm error|npm ERR!) (?:code )?(?:E404|ETARGET|ELSPROBLEMS)", re.IGNORECASE,
)
TS_ERROR = re.compile(r"\b(?:error )?TS\d{4}:")
NEXT_FAILURE = re.compile(r"error occurred prerendering|export encountered errors|failed to load next\.config|export failed|failed to compile", re.IGNORECASE)
OTHER_FAILURE = re.compile(
    r"^\s*(?:##\[error\]|Error:|ERROR:|npm (?:ERR!|error)|FAIL\s|Failed to compile|"
    r"Module not found:|Cannot find module|Type error:|\S+.*(?:ERR_MODULE_NOT_FOUND|TS\d{4}:))", re.IGNORECASE,
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

            if len(test_name) <= 512 and test_name not in tests:
                tests.append(test_name)

    return tests


def extract_error(
    lines: list[str],
) -> tuple[str | None, str | None, int | None]:
    """Return the last recognizable exception or standalone error line."""
    result = (None, None, None)
    priority = -1

    for index, line in enumerate(lines):
        if WARNING.search(line) or line.startswith(("##[group]", "##[debug]")):
            continue
        match = ERROR_RE.search(line.lstrip())
        if EXIT.search(line):
            rank, kind, message = 0, None, line
        elif match:
            rank, kind, message = 3, match["type"], match["message"].strip()
            if match["code"]:
                kind, message = None, match[0]
        elif any(pattern.search(line) for pattern in (RUNNER, PLATFORM, PY_DEPS, JS_DEPS, TS_ERROR)):
            rank, kind, message = 3, None, line
        elif NEXT_FAILURE.search(line) or OTHER_FAILURE.search(line):
            rank, kind, message = 2, None, line
        elif PYTEST_FAILURE_RE.fullmatch(line.strip()):
            rank, kind, message = 1, None, line
        else:
            continue
        if rank >= priority:
            result = (kind, message[:2000], index)
            priority = rank

    return result


def in_step(line: str, started_at: datetime, completed_at: datetime) -> bool:
    match = GITHUB_TIMESTAMP_RE.match(line)
    if not match:
        return False
    try:
        return started_at <= datetime.fromisoformat(match[1]) <= completed_at
    except ValueError:
        return False


def identify_failure(
    failed_step: str | None, command: str, excerpt: str, observed: str, pytest: bool,
) -> tuple[Runtime, FailureOrigin]:
    step = f"{failed_step or ''} {command}"
    text = f"{step}\n{observed}\n{excerpt}"
    if RUNNER.search(observed):
        return "runner", "runner_environment"
    if PLATFORM.search(observed):
        return "github_action", "github_platform"

    runtime: Runtime = "unknown"
    if PY_DEPS.search(text) or re.search(r"\b(?:pip3?|uv)\s+(?:pip\s+)?(?:install|sync|lock|resolve)\b", step, re.IGNORECASE):
        return "python", "dependency_installation"
    if JS_DEPS.search(text) or re.search(r"\b(?:npm|pnpm|yarn)\s+(?:ci|install|add)\b", step, re.IGNORECASE):
        return "nodejs", "dependency_installation"
    if re.search(r"\bnext(?:\.js|\s+(?:build|export)|\.config\.)|prerendering|export encountered errors", text, re.IGNORECASE):
        return "nextjs", "build_tool"
    if TS_ERROR.search(observed) or re.search(r"\btsc\b", step, re.IGNORECASE):
        return "typescript", "build_tool"
    if pytest or re.search(r"\bpytest\b", text, re.IGNORECASE):
        return "python", "test_execution"
    if re.search(r"\b(?:vitest|jest)\b", text, re.IGNORECASE):
        return "javascript", "test_execution"
    if re.search(r"\b(?:npm|pnpm|yarn)\s+(?:run\s+)?(?:build|test)\b", step, re.IGNORECASE):
        origin: FailureOrigin = "test_execution" if re.search(r"\btest\b", step, re.IGNORECASE) else "build_tool"
        return "nodejs", origin
    if re.search(r"\b(?:npm (?:error\b|ERR!)|ERR_PNPM_|(?:pnpm|yarn) (?:run|error)\b)", excerpt, re.IGNORECASE):
        return "nodejs", "unknown"
    if re.search(r"\bpython(?:3(?:\.\d+)?)?\b|Traceback \(most recent call last\)|\b(?:ModuleNotFound|Indentation|Import)Error:", text, re.IGNORECASE) or re.search(r'\.py(?::\d+|", line \d+)', excerpt):
        runtime = "python"
    elif re.search(r"ERR_MODULE_NOT_FOUND|MODULE_NOT_FOUND|node:internal|Cannot find module", text) or re.search(r"\bnode\s+\S+", step):
        runtime = "nodejs"
    elif re.search(r"\.(?:tsx?|mts|cts):\d+", excerpt):
        runtime = "typescript"
    elif re.search(r"\.(?:jsx?|mjs|cjs):\d+|\b(?:Reference|Range)Error:", text):
        runtime = "javascript"
    return runtime, "application_code" if runtime != "unknown" else "unknown"


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
    *,
    step_started_at: datetime | None = None,
    step_completed_at: datetime | None = None,
) -> FailurePacket:
    """
    Convert GitHub Actions job logs into a compact
    failure packet for downstream investigation.
    """

    job_lines = clean_log(raw_log or "")
    lines = job_lines
    if step_started_at and step_completed_at and raw_log:
        step_lines = [
            line for raw, line in zip(raw_log.splitlines(), job_lines, strict=True)
            if in_step(raw, step_started_at, step_completed_at)
        ]
        if any(step_lines):
            lines = step_lines
    groups = [(index, match[1]) for index, line in enumerate(lines) if (match := RUN_GROUP.match(line))]
    named_action = re.search(ACTION, failed_step or "")
    matching = [
        index for index, command in groups
        if failed_step and (command == failed_step.removeprefix("Run ") or named_action and command == named_action[0])
    ]
    if matching:
        start = matching[-1]
        end = next((index for index, _ in groups if index > start), len(lines))
        lines = lines[start:end]

    error_type, error_message, error_index = extract_error(lines)
    command = next((match[1] for line in reversed(lines[:error_index + 1] if error_index is not None else lines)
                    if (match := RUN_GROUP.match(line))), "")
    action = named_action[0] if named_action else command if re.fullmatch(ACTION, command) else None
    anchor = error_index if error_index is not None else max(0, len(lines) - 10)
    excerpt_lines = lines[max(0, anchor - 25):anchor + 11]
    # Keep the selected line in the excerpt even when surrounding frames are unusually long.
    relevant_log = "\n".join(line[:500] if index != min(anchor, 25) else line[:2000]
                             for index, line in enumerate(excerpt_lines))
    failing_tests = extract_failing_tests(lines)[:20]
    signature = f"{error_type}: {error_message}" if error_type else error_message
    routing_excerpt = "\n".join(line for line in excerpt_lines if not WARNING.search(line))
    if not action and (frame := ACTION_FRAME.search(routing_excerpt)):
        action = f"{frame[1]}/{frame[2]}@{frame[3]}"
    runtime, origin = identify_failure(failed_step or job_name, command, routing_excerpt, signature or "", bool(failing_tests))
    if action and origin != "runner_environment":
        origin = "third_party_action"
        if runtime in {"javascript", "nodejs", "typescript"}:
            runtime = "nodejs"
        elif runtime != "python":
            runtime = "nodejs" if error_type in {"TypeError", "ReferenceError", "RangeError"} else "github_action"

    return FailurePacket(
        job_id=job_id,
        job_name=job_name,
        failed_step=failed_step,
        action=action,
        runtime=runtime,
        failure_origin=origin,
        warnings=list(dict.fromkeys([
            *(line[:300] for line in lines if WARNING.search(line)),
            *(line[:300] for line in job_lines if NODE_WARNING.search(line)),
        ]))[:5],
        error_type=error_type,
        error_message=error_message,
        error_signature=signature,
        failing_tests=failing_tests,
        relevant_log=relevant_log,
        exit_code=exit_code,
        extraction_status=(
            "log_unavailable" if raw_log is None else "extracted"
            if error_message or failing_tests
            else "no_failure_found"
        ),
    )
