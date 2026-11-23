
import re

from app.schema.failure_classification import (
    FailureClassification,
)
from app.schema.investigation_context import (
    InvestigationContext,
)


INFRA_PATTERNS = [
    re.compile(r"connection timed out", re.I),
    re.compile(r"could not resolve host", re.I),
    re.compile(r"temporary failure in name resolution", re.I),
    re.compile(r"no space left on device", re.I),
    re.compile(r"runner.*lost communication", re.I),
    re.compile(r"rate limit exceeded", re.I),
]

DEPENDENCY_PATTERNS = [
    re.compile(r"resolutionimpossible", re.I),
    re.compile(r"no matching distribution found", re.I),
    re.compile(r"could not find a version that satisfies", re.I),
    re.compile(r"dependency conflict", re.I),
    re.compile(r"failed to build wheel", re.I),
]

CODE_ERROR_TYPES = {
    "AssertionError",
    "SyntaxError",
    "IndentationError",
    "NameError",
    "TypeError",
    "AttributeError",
    "ImportError",
    "ModuleNotFoundError",
    "ValueError",
    "KeyError",
    "IndexError",
    "ZeroDivisionError",
}

DEPENDENCY_FILES = {
    "requirements.txt",
    "pyproject.toml",
    "poetry.lock",
    "uv.lock",
    "pdm.lock",
    "pipfile.lock",
}


def classify_failure(
    context: InvestigationContext,
) -> FailureClassification:
    """
    Classify collected CI evidence using deterministic rules.

    Classification is preliminary and does not establish
    the definitive root cause.
    """

    packets = context.failure_packets

    if not packets:
        return FailureClassification(
            category="unknown",
            confidence=0.0,
            reasons=["No failure packets available"],
        )

    error_types = {
        packet.error_type
        for packet in packets
        if packet.error_type
    }

    # Use reduced evidence rather than full job logs.
    evidence = "\n".join(
        (
            (packet.error_message or "")
            + "\n"
            + packet.relevant_log[-8000:]
        )
        for packet in packets
    )

    changed_files = {
        file.filename.lower()
        for file in context.changed_files
    }

    dependency_changed = any(
        filename.rsplit("/", 1)[-1] in DEPENDENCY_FILES
        for filename in changed_files
    )

    # 1. Infrastructure / environment errors
    if any(
        pattern.search(evidence)
        for pattern in INFRA_PATTERNS
    ):
        return FailureClassification(
            category="infra_config",
            confidence=0.8,
            reasons=[
                "Infrastructure or environment error detected"
            ],
        )

    # 2. Explicit dependency installation failures
    if any(
        pattern.search(evidence)
        for pattern in DEPENDENCY_PATTERNS
    ):
        reasons = [
            "Dependency installation or resolution failure detected"
        ]

        if dependency_changed:
            reasons.append(
                "Dependency configuration changed in commit diff"
            )

        return FailureClassification(
            category="dependency",
            confidence=0.85 if dependency_changed else 0.75,
            reasons=reasons,
        )

    # 3. Python code errors
    code_errors = error_types.intersection(
        CODE_ERROR_TYPES
    )

    if code_errors:
        reasons = [
            f"Detected Python error: {', '.join(sorted(code_errors))}"
        ]

        confidence = 0.7

        if context.correlations:
            reasons.append(
                f"{len(context.correlations)} failing-file "
                "correlation(s) found in commit diff"
            )
            confidence = 0.85

        return FailureClassification(
            category="code_bug",
            confidence=confidence,
            reasons=reasons,
        )

    # 4. Unknown
    return FailureClassification(
        category="unknown",
        confidence=0.2,
        reasons=[
            "No reliable classification rule matched"
        ],
    )
