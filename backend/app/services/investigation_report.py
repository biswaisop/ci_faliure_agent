import re
from collections.abc import AsyncIterator
from urllib.parse import quote

from app.schema.diagnosis import UNCONFIRMED_ROOT_CAUSE, DiagnosisResult
from app.schema.failure_packet import FailurePacket
from app.schema.investigation_context import ChangedFile
from app.services.investigation_context_builder import logged_changes


def inline_code(value: str) -> str:
    value = " ".join(value.splitlines())
    delimiter = "`" * (1 + max(map(len, re.findall(r"`+", value)), default=0))
    return f"{delimiter} {value} {delimiter}"


async def report_sections(
    diagnosis: DiagnosisResult, packets: list[FailurePacket], files: list[ChangedFile],
) -> AsyncIterator[str]:
    primary = next((packet for packet in packets if diagnosis.evidence_ids[:1] == [f"log:{packet.job_id}"]), None)
    step = ""
    if primary and primary.failed_step and primary.action:
        step = f"{inline_code(primary.failed_step)} failed while running {inline_code(primary.action)}.\n\n"
    yield f"### What failed\n\n{step}{diagnosis.summary}\n\n"

    confirmed = diagnosis.root_cause and diagnosis.root_cause != UNCONFIRMED_ROOT_CAUSE
    if confirmed:
        yield f"### Root cause\n\n{diagnosis.root_cause}\n\n"
    if diagnosis.possible_cause:
        title = "Possible cause" if confirmed else "Likely cause"
        yield f"### {title}\n\n{diagnosis.possible_cause}\n\n"

    suspect = diagnosis.suspected_file
    matches = logged_changes(packets, files)
    if not suspect and len(matches) == 1:
        suspect = next(iter(matches))
    if suspect:
        label = inline_code(suspect)
        if any(file.filename == suspect for file in files):
            # Match the two URL encodings used by the dashboard's evidence anchors.
            safe = "!~*'()"
            anchor = f"evidence-{quote(f'diff:{suspect}', safe=safe)}"
            label = f"[{label}](#{quote(anchor, safe=safe)})"
        yield f"### Suspected file\n\n{label}\n\n"
        if not diagnosis.possible_cause and suspect in matches:
            yield f"The failed job printed this changed line {inline_code(matches[suspect])}.\n\n"

    yield (
        f"- **Reported failure confidence** {diagnosis.confidence.replace('_', ' ')}\n"
        f"- **Regression attribution** {diagnosis.regression_attribution.replace('_', ' ')}\n\n"
    )
    if diagnosis.limitations:
        yield "### Investigation limitations\n\n" + "\n".join(
            "- " + item.replace("\n", "\n  ") for item in diagnosis.limitations
        ) + "\n\n"
    if diagnosis.suggested_actions:
        yield "### Suggested next actions\n\n" + "\n".join(
            "1. " + item.replace("\n", "\n   ") for item in diagnosis.suggested_actions
        ) + "\n"
