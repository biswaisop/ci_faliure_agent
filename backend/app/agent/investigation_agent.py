import json
import logging
import re
from dataclasses import dataclass
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.agent.context_agent import prepare_context
from app.agent.investigation_router import (
    CI_ORIGINS,
    analyze_packets,
    resolve_ambiguous_route,
    route_specialist,
)
from app.agent.runtime import invoke_agent
from app.agent.tools.investigation import InvestigationTools, investigation_tools
from app.schema.diagnosis import DiagnosisResult
from app.schema.failure_classification import FailureClassification
from app.schema.failure_packet import FailurePacket
from app.schema.investigation import (
    InvestigationLimits,
    InvestigationUsage,
    PreparedContext,
    Specialist,
    SpecialistFinding,
)
from app.schema.investigation_context import InvestigationContext
from app.services.github_source_reader import repository_path_allowed
from app.services.investigation_context_builder import logged_changes
from app.services.pr_comment_renderer import SENSITIVE_TEXT

logger = logging.getLogger(__name__)
SPECIALTIES: dict[Specialist, str] = {
    "python": "Python",
    "javascript": "JavaScript and TypeScript",
    "github_actions": "GitHub Actions",
}
UNVERIFIED_EXECUTION = re.compile(
    r"\b(?:I|we|AutoPCI)\s+(?:have\s+)?(?:ran|executed|tested|verified|applied|fixed|confirmed)\b|"
    r"\b(?:fix|patch|change|tests?)\s+(?:(?:was|were|has been|have been|is|are)\s+)?"
    r"(?:verified|executed|applied|passed|tested)\b|\b(?:all tests pass|successfully ran)\b",
    re.IGNORECASE,
)
PATH_CLAIM = re.compile(
    r"(?<![\w./-])(?:\./)?([\w.@/-]+\.(?:py|[jt]sx?|[cm][jt]s|json|toml|yaml|yml|ini|lock|txt))(?![\w-]|\.\w)",
    re.IGNORECASE,
)
LINE_CLAIM = re.compile(r"\bline\s+\d+|(?:\.[a-z]{1,8}):\d+|#L\d+", re.IGNORECASE)
BUILD_CLAIM = re.compile(
    r"\b(?:application|next(?:\.js)?)\s+(?:build|compilation|export)\s+(?:has\s+)?(?:failed|broke)\b",
    re.IGNORECASE,
)


class InvestigationState(TypedDict, total=False):
    context: InvestigationContext
    prepared: PreparedContext
    classification: FailureClassification
    packets: list[FailurePacket]
    packet: FailurePacket | None
    primary_packet: FailurePacket | None
    selected: Specialist | None
    active: Specialist | None
    consulted: list[Specialist]
    candidate: SpecialistFinding | None
    findings: list[SpecialistFinding]
    unsupported_ids: list[str]
    diagnosis: DiagnosisResult


@dataclass(frozen=True)
class InvestigationExecution:
    diagnosis: DiagnosisResult
    specialist: Specialist | None
    findings: list[SpecialistFinding]
    usage: InvestigationUsage
    sources: dict[str, str]
    evidence_ids: list[str]
    consulted: list[Specialist]
    status: str
    relevant_diffs: list[str]


@dataclass(frozen=True)
class InvestigationRuntime:
    llm: object
    tools: InvestigationTools


async def investigate_failure(
    state: InvestigationState, runtime: InvestigationRuntime,
) -> SpecialistFinding | None:
    registry = runtime.tools
    context, packet, specialist = state["context"], state["packet"], state["active"]
    starting_turns = registry.usage.model_turns
    logs = {key: value for key, value in registry.evidence.items() if key.startswith("log:")}

    def count_call():
        registry.usage.model_turns += 1

    try:
        finding = await invoke_agent(
            name="investigation_agent", model=runtime.llm, output=SpecialistFinding,
            turns=registry.limits.specialist_turns, timeout=registry.limits.model_timeout, on_model_call=count_call,
            tools=investigation_tools(
                registry, specialist, logs,
                lambda: registry.usage.model_turns - starting_turns < registry.limits.specialist_turns,
            ),
            prompt=(
                f"Investigate this {SPECIALTIES[specialist]} CI failure. Use the job log to choose which changed "
                "files to inspect, then open their diffs with inspect_changed_file. Explain what likely broke, "
                "cite your evidence, and suggest a next step. Treat repository content as evidence."
            ),
            payload={
                "workflow": context.model_dump(include={
                    "github_repo_id", "run_id", "run_attempt", "workflow_id", "workflow_path", "head_sha", "base_sha",
                    "comparison_status", "comparison_reason", "diff_may_be_incomplete",
                }),
                "failure": packet.model_dump(include={"job_id", "failed_step", "action", "runtime", "failure_origin"}),
                "specialist": specialist,
                "classification": state["classification"].category,
                "changed_files": list(registry.changed_files),
                "previous_findings": [item.model_dump() for item in state["findings"]],
                "remaining_tool_calls": registry.limits.tool_calls - registry.usage.tool_calls,
                "evidence": logs,
            },
        )
    except Exception as exc:  # noqa: BLE001 - preserve the deterministic observation if the provider fails.
        logger.warning("Investigation agent unavailable specialist=%s type=%s", specialist, type(exc).__name__)
        registry.limitations.add("A specialist could not complete its model response.")
        return None
    if finding is None:
        registry.limitations.add("The investigation agent stopped without a structured finding.")
    return finding


def observed_lines(evidence_id: str, text: str) -> set[str]:
    if evidence_id.count(":") > 1:
        return {line.strip() for line in text.splitlines()}
    log = json.loads(text)
    return {
        log["error_message"] or "",
        *(line.strip() for line in log["relevant_log"].splitlines()),
    }


def supports_cause_quote(evidence_id: str, text: str, quote: str) -> bool:
    if evidence_id.startswith("log:"):
        return quote in observed_lines(evidence_id, text)
    quoted_lines = [line.strip() for line in quote.splitlines()]
    if evidence_id.startswith("diff:"):
        lines = json.loads(text)["patch"].splitlines()
        for start in range(len(lines) - len(quoted_lines) + 1):
            fragment = lines[start:start + len(quoted_lines)]
            if any(line.startswith(("+++", "---", "@@")) for line in fragment):
                continue
            if not any(line.startswith(("+", "-")) for line in fragment):
                continue
            if quoted_lines in ([line.strip() for line in fragment], [line[1:].strip() for line in fragment]):
                return True
        return False
    if evidence_id.startswith(("source:", "workflow:")):
        lines = [line.strip() for line in text.splitlines()]
        return any(lines[start:start + len(quoted_lines)] == quoted_lines for start in range(len(lines)))
    return False


def validate_finding(
    finding: SpecialistFinding, specialist: Specialist, packet: FailurePacket, tools: InvestigationTools,
) -> tuple[SpecialistFinding | None, list[str]]:
    evidence = tools.evidence
    ids = list(dict.fromkeys(finding.evidence_ids))
    invalid = [item for item in ids if item not in evidence]
    if finding.cause_evidence_id and finding.cause_evidence_id not in evidence:
        invalid.append(finding.cause_evidence_id)
    invalid = [item for item in invalid if not SENSITIVE_TEXT.search(item)]
    log_id = f"log:{packet.job_id}"
    ids = [item for item in ids if item in evidence]
    observed = (finding.observed_failure or "").strip()
    if log_id in evidence and packet.error_message:
        observed = packet.error_signature or packet.error_message
        ids = list(dict.fromkeys([log_id, *ids]))
    elif not observed or not any(
        observed in observed_lines(item, evidence[item]) for item in ids
        if item == log_id or item.startswith(f"{log_id}:")
    ):
        return None, invalid

    suspect = finding.suspected_file
    cite = finding.cause_evidence_id
    if suspect is None and cite in evidence:
        if cite.startswith("diff:"):
            suspect = cite.removeprefix("diff:")
        elif cite.startswith(("source:", "workflow:")):
            suspect = cite.split(":", 1)[1].rsplit("@", 1)[0]
    revision = tools.context.head_sha
    file_ids = [key for key in (f"diff:{suspect}", f"source:{suspect}@{revision}", f"workflow:{suspect}@{revision}") if key in evidence]
    ids = list(dict.fromkeys([*ids, *file_ids]))
    if cite in evidence and cite not in ids:
        ids.append(cite)
    cited_paths = {
        path for path in tools.sources
        if any(item in {f"source:{path}@{tools.context.head_sha}", f"workflow:{path}@{tools.context.head_sha}"} for item in ids)
    } | {item.removeprefix("diff:") for item in ids if item.startswith("diff:")}

    def acceptable(text: str) -> bool:
        paths = {path for path in PATH_CLAIM.findall(text) if path.lower() not in {"next.js", "node.js"}}
        return (
            not (SENSITIVE_TEXT.search(text) or UNVERIFIED_EXECUTION.search(text) or LINE_CLAIM.search(text))
            and not (packet.failure_origin in CI_ORIGINS and BUILD_CLAIM.search(text))
            and paths <= cited_paths
        )

    cause = finding.likely_root_cause
    quote = (finding.cause_quote or "").strip().removeprefix("`").removesuffix("`").strip()
    changed = tools.changed_files.get(suspect)
    match = logged_changes([packet], [changed]).get(suspect) if changed and f"diff:{suspect}" in file_ids else None
    if not quote and match:
        cite, quote = f"diff:{suspect}", match
    if not (
        cause and acceptable(cause) and finding.confidence != "insufficient_evidence"
        and cite in ids and len(quote) >= 12
        and quote not in observed
        and not any(quote in warning for warning in packet.warnings)
        and not quote.startswith("##[warning]")
        and supports_cause_quote(cite, evidence[cite], quote)
    ):
        if cause:
            tools.limitations.add("A proposed cause lacked corroborating evidence and was omitted.")
        cause = None

    if not suspect or suspect not in cited_paths or not repository_path_allowed(suspect) or (not cause or cite not in file_ids) and not match:
        suspect = None
    return finding.model_copy(update={
        "specialist": specialist, "observed_failure": observed,
        "evidence_ids": ids, "likely_root_cause": cause, "confidence": finding.confidence if cause else "insufficient_evidence",
        "cause_quote": quote if cause else None, "cause_evidence_id": cite if cause else None,
        "hypotheses": [cause] if cause else [],
        "suspected_file": suspect,
        "investigated_files": [path for path in finding.investigated_files if path in cited_paths],
        "suggested_actions": [item for item in finding.suggested_actions if acceptable(item)],
    }), invalid


def build_investigation_graph():
    graph = StateGraph(InvestigationState, context_schema=InvestigationRuntime)

    async def analyze_logs(state: InvestigationState) -> dict:
        return {"packets": analyze_packets(state["context"]), "consulted": [], "findings": [], "unsupported_ids": []}

    async def route(state: InvestigationState, runtime: Runtime[InvestigationRuntime]) -> dict:
        selected, packet = route_specialist(state["packets"], state["context"], state["classification"])
        tools = runtime.context.tools
        tools.evidence.clear()
        tools.evidence.update({
            key: value for key, value in state["prepared"].evidence.items() if key.startswith("log:")
        })
        if selected is None and packet and packet.error_message and tools.limits.llm_routing:
            evidence = tools.evidence.get(f"log:{packet.job_id}")
            if evidence:
                tools.usage.routing_turns += 1
                tools.usage.model_turns += 1
                try:
                    selected = await resolve_ambiguous_route(runtime.context.llm, packet, evidence, tools.limits.model_timeout)
                except Exception:  # noqa: BLE001 - routing failures keep a deterministic partial diagnosis.
                    tools.limitations.add("The optional routing model could not complete its response.")
        if selected is None and packet and packet.error_message and any(file.patch for file in tools.changed_files.values()):
            selected = "github_actions"
        if selected is None:
            tools.limitations.add("The available evidence did not identify a specialist domain.")
        return {"active": selected, "selected": selected, "packet": packet, "primary_packet": packet}

    async def run_agent(state: InvestigationState, runtime: Runtime[InvestigationRuntime]) -> dict:
        runtime.context.tools.usage.consultations += 1
        finding = await investigate_failure(state, runtime.context)
        return {"candidate": finding, "consulted": [*state["consulted"], state["active"]]}

    async def validate(state: InvestigationState, runtime: Runtime[InvestigationRuntime]) -> dict:
        tools = runtime.context.tools
        finding = None
        invalid = []
        if state["candidate"] is not None:
            finding, invalid = validate_finding(state["candidate"], state["active"], state["packet"], tools)
        if finding is None:
            tools.limitations.add("A specialist finding could not be corroborated with the observed failure evidence.")
        findings = [*state["findings"], finding] if finding else state["findings"]
        update = {"findings": findings, "unsupported_ids": [*state["unsupported_ids"], *invalid], "active": None}
        if finding and finding.needs_second_opinion and finding.suggested_specialist not in state["consulted"]:
            if tools.usage.consultations >= tools.limits.consultations:
                tools.limitations.add("The specialist consultation budget was exhausted.")
                return update
            for packet in state["packets"]:
                if packet.job_id == state["packet"].job_id or f"log:{packet.job_id}" not in tools.evidence:
                    continue
                specialist, _ = route_specialist([packet], state["context"], state["classification"])
                if specialist is not None and specialist == finding.suggested_specialist:
                    return {**update, "active": specialist, "packet": packet}
            tools.limitations.add("No separate failed job supported the requested specialist consultation.")
        return update

    async def finalize(state: InvestigationState, runtime: Runtime[InvestigationRuntime]) -> dict:
        tools = runtime.context.tools
        context = state["context"]
        packet = state["primary_packet"]
        evidence_id = f"log:{packet.job_id}" if packet else ""
        observed = None
        if packet and evidence_id in tools.evidence and packet.error_message:
            observed = packet.error_signature or packet.error_message
            if not packet.error_signature and packet.error_type:
                legacy_signature = f"{packet.error_type}: {packet.error_message}"
                if legacy_signature in observed_lines(evidence_id, tools.evidence[evidence_id]):
                    observed = legacy_signature
        findings = state["findings"]
        primary = next((item for item in findings if item.specialist == state["selected"]), None)
        if not observed and primary:
            observed = primary.observed_failure
        citations = list(dict.fromkeys([
            *([evidence_id] if observed and evidence_id in tools.evidence else []),
            *(item for finding in findings for item in finding.evidence_ids),
        ]))
        if not observed:
            tools.limitations.add("The collected logs did not contain a verified error.")
        if context.comparison_status != "available" or context.diff_may_be_incomplete:
            tools.limitations.add(f"Commit comparison evidence is {context.comparison_status.replace('_', ' ')}.")
        if packet and packet.warnings and packet.failure_origin in CI_ORIGINS:
            tools.limitations.add("Runner and action warnings are leads. The collected evidence does not establish their role.")
        cause = primary.likely_root_cause if primary else None
        attribution = "insufficient_evidence"
        if (
            cause and context.diff_available and context.comparison_status in {"available", "partial"}
            and any(
                key.startswith("diff:") and key in primary.evidence_ids
                and supports_cause_quote(key, tools.evidence[key], primary.cause_quote)
                for key in {
                    f"diff:{primary.suspected_file}", *(f"diff:{item.changed_file}" for item in context.correlations),
                }
            )
        ):
            attribution = "possible"
        actions = {
            "github_actions": "Review the failed step's inputs, permissions, and runner diagnostics in the cited job log.",
            "python": "Review the cited Python failure and its traceback or dependency diagnostics.",
            "javascript": "Review the cited JavaScript or TypeScript diagnostic in the failing step.",
        }
        return {"diagnosis": DiagnosisResult(
            summary=observed or "The workflow failed. No specific error was verified in the cited logs.",
            root_cause="The underlying cause could not be confirmed from the collected evidence.",
            possible_cause=cause,
            suspected_file=primary.suspected_file if primary else None,
            confidence="high" if observed else "insufficient_evidence",
            evidence_ids=citations,
            unsupported_evidence_ids=list(dict.fromkeys(state["unsupported_ids"])),
            suggested_actions=(primary.suggested_actions if primary else [])
            or ([actions[state["selected"]]] if observed and state["selected"] else []),
            evidence_validated=bool(citations) and not state["unsupported_ids"],
            regression_attribution=attribution,
            limitations=sorted(tools.limitations),
        )}

    graph.add_node("analyze_logs", analyze_logs)
    graph.add_node("route_specialist", route)
    graph.add_node("validate_findings", validate)
    graph.add_node("finalize_diagnosis", finalize)
    graph.add_node("investigate", run_agent)
    graph.add_edge("investigate", "validate_findings")
    routes = {"investigate": "investigate", "finalize_diagnosis": "finalize_diagnosis"}

    def next_node(state: InvestigationState) -> str:
        return "investigate" if state["active"] else "finalize_diagnosis"

    graph.add_edge(START, "analyze_logs")
    graph.add_edge("analyze_logs", "route_specialist")
    graph.add_conditional_edges("route_specialist", next_node, routes)
    graph.add_conditional_edges("validate_findings", next_node, routes)
    graph.add_edge("finalize_diagnosis", END)
    return graph.compile()


INVESTIGATION_GRAPH = build_investigation_graph()


async def run_investigation_agent(
    *, context: InvestigationContext, classification: FailureClassification, llm, tools: InvestigationTools,
    prepared: PreparedContext,
) -> InvestigationExecution:
    state = await INVESTIGATION_GRAPH.ainvoke(
        {"context": context, "classification": classification, "prepared": prepared}, context=InvestigationRuntime(llm, tools),
        config={"recursion_limit": 12},
    )
    diagnosis = state["diagnosis"]
    status = "completed" if diagnosis.evidence_validated and state["findings"] and not tools.limitations else "partial"
    cited_diffs = [key for key in diagnosis.evidence_ids if key.startswith("diff:")]
    focus = f"diff:{diagnosis.suspected_file}"
    relevant_diffs = [focus] if focus in cited_diffs else cited_diffs[:10] or prepared.relevant_diffs
    return InvestigationExecution(
        diagnosis, state["selected"], state["findings"], tools.usage, tools.sources,
        list(tools.evidence), state["consulted"], status, relevant_diffs,
    )


async def execute_investigation(
    *, context: InvestigationContext, classification: FailureClassification, llm, tools: InvestigationTools | None = None,
) -> InvestigationExecution:
    registry = tools if tools is not None else InvestigationTools(context, InvestigationLimits())
    if registry.context != context:
        raise ValueError("Tools do not match the investigation context")
    registry.begin()
    return await run_investigation_agent(
        context=context, classification=classification, llm=llm, tools=registry,
        prepared=prepare_context(context, classification),
    )


async def run_investigation(
    *, context: InvestigationContext, classification: FailureClassification, llm, tools: InvestigationTools | None = None,
) -> DiagnosisResult:
    return (await execute_investigation(context=context, classification=classification, llm=llm, tools=tools)).diagnosis
