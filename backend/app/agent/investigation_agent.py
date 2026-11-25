import json
import re
from dataclasses import dataclass
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.agent.investigation_router import (
    CI_ORIGINS,
    analyze_packets,
    resolve_ambiguous_route,
    route_specialist,
)
from app.agent.investigation_specialists import InvestigationRuntime, build_specialist
from app.agent.investigation_tools import FAILURE_CONTEXT_BYTES, InvestigationTools
from app.schema.diagnosis import DiagnosisResult
from app.schema.failure_classification import FailureClassification
from app.schema.failure_packet import FailurePacket
from app.schema.investigation import (
    InvestigationLimits,
    InvestigationUsage,
    Specialist,
    SpecialistFinding,
)
from app.schema.investigation_context import InvestigationContext
from app.services.pr_comment_renderer import SENSITIVE_TEXT

MAX_LOG_CHARS = 5000
MAX_PATCH_CHARS = 4000
MAX_FILES = 15
MAX_PACKETS = 5
SPECIALISTS: tuple[Specialist, ...] = ("python", "javascript", "github_actions")
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


def prepare_evidence(state: InvestigationState) -> dict:
    """Select bounded log and diff evidence shared with the fix workflow."""
    context = state["context"]
    evidence: dict[str, str] = {}
    selected = state.get("packet")
    packets = state.get("packets", context.failure_packets)
    ordered = sorted(packets, key=lambda packet: packet is not selected)
    for packet in ordered[:MAX_PACKETS]:
        log = packet.relevant_log
        error_offset = log.find(packet.error_message or "")
        if error_offset >= MAX_LOG_CHARS - 2000:
            log = log[max(0, error_offset - 2000):]
        evidence[f"log:{packet.job_id}"] = json.dumps({
            "job_name": packet.job_name, "failed_step": packet.failed_step, "action": packet.action,
            "runtime": packet.runtime, "failure_origin": packet.failure_origin, "warnings": packet.warnings[:5],
            "error_type": packet.error_type, "error_message": packet.error_message,
            "failing_tests": packet.failing_tests[:20], "relevant_log": log[:MAX_LOG_CHARS],
        }, ensure_ascii=False)
    correlated = {item.changed_file for item in context.correlations}
    ordered_files = sorted(context.changed_files, key=lambda file: (file.filename not in correlated, file.filename))
    for file in ordered_files[:MAX_FILES]:
        evidence[f"diff:{file.filename}"] = json.dumps({
            "filename": file.filename, "status": file.status, "previous_filename": file.previous_filename,
            "patch": file.patch[:MAX_PATCH_CHARS], "patch_status": file.patch_status,
            "truncated": file.truncated or len(file.patch) > MAX_PATCH_CHARS,
        }, ensure_ascii=False)
    safe = {}
    remaining = FAILURE_CONTEXT_BYTES
    for key, value in evidence.items():
        size = len((key + value).encode("utf-8"))
        if size <= remaining and not SENSITIVE_TEXT.search(key + value):
            safe[key] = value
            remaining -= size
    return {"evidence": safe}


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
    if evidence_id.startswith("diff:"):
        lines = json.loads(text)["patch"].splitlines()
        return any(line.startswith("+") and line[1:].strip() == quote for line in lines)
    return evidence_id.startswith(("source:", "workflow:")) and any(quote == line.strip() for line in text.splitlines())


def validate_finding(
    finding: SpecialistFinding, specialist: Specialist, packet: FailurePacket, tools: InvestigationTools,
) -> tuple[SpecialistFinding | None, list[str]]:
    evidence = tools.evidence
    ids = list(dict.fromkeys(finding.evidence_ids))
    invalid = [item for item in ids if item not in evidence]
    if finding.cause_evidence_id and finding.cause_evidence_id not in evidence:
        invalid.append(finding.cause_evidence_id)
    if (
        invalid or not ids or finding.specialist != specialist or not finding.observed_failure
        or not any(
            item == f"log:{packet.job_id}" or item.startswith(f"log:{packet.job_id}:") for item in ids
        )
        or not any(
            finding.observed_failure.strip() in observed_lines(item, evidence[item])
            for item in ids if item == f"log:{packet.job_id}" or item.startswith(f"log:{packet.job_id}:")
        )
        or not set(finding.investigated_files) <= tools.sources.keys()
    ):
        return None, [item for item in invalid if not SENSITIVE_TEXT.search(item)]
    if packet.error_message and finding.observed_failure.strip() not in {
        packet.error_message.strip(), (packet.error_signature or "").strip(),
    } and packet.error_message.strip() not in finding.observed_failure:
        return None, []
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
    quote = (finding.cause_quote or "").strip()
    cite = finding.cause_evidence_id
    if not (
        cause and acceptable(cause) and finding.confidence != "insufficient_evidence"
        and cite in ids and len(quote) >= 12
        and quote not in (finding.observed_failure or "") and quote not in (packet.error_signature or "")
        and not any(quote in warning for warning in packet.warnings)
        and not quote.startswith("##[warning]")
        and supports_cause_quote(cite, evidence[cite], quote)
    ):
        if cause:
            tools.limitations.add("A proposed cause lacked corroborating evidence and was omitted.")
        cause = None
    return finding.model_copy(update={
        "evidence_ids": ids, "likely_root_cause": cause, "confidence": finding.confidence if cause else "insufficient_evidence",
        "cause_quote": quote if cause else None, "cause_evidence_id": cite if cause else None,
        "hypotheses": [cause] if cause else [],
        "suggested_actions": [item for item in finding.suggested_actions if acceptable(item)],
    }), []


def build_investigation_graph():
    specialist_graphs = {name: build_specialist(name) for name in SPECIALISTS}
    graph = StateGraph(InvestigationState, context_schema=InvestigationRuntime)

    def analyze_logs(state: InvestigationState) -> dict:
        return {"packets": analyze_packets(state["context"]), "consulted": [], "findings": [], "unsupported_ids": []}

    async def route(state: InvestigationState, runtime: Runtime[InvestigationRuntime]) -> dict:
        selected, packet = route_specialist(state["packets"], state["context"], state["classification"])
        tools = runtime.context.tools
        tools.evidence.update(prepare_evidence({**state, "packet": packet})["evidence"])
        if selected is None and packet and packet.error_message and tools.limits.llm_routing:
            evidence = tools.evidence.get(f"log:{packet.job_id}")
            if evidence:
                tools.usage.routing_turns += 1
                tools.usage.model_turns += 1
                try:
                    selected = await resolve_ambiguous_route(runtime.context.llm, packet, evidence, tools.limits.model_timeout)
                except Exception:  # noqa: BLE001 - routing failures keep a deterministic partial diagnosis.
                    tools.limitations.add("The optional routing model could not complete its response.")
        if selected is None:
            tools.limitations.add("The available evidence did not identify a specialist domain.")
        return {"active": selected, "selected": selected, "packet": packet, "primary_packet": packet}

    def specialist_node(name: Specialist):
        async def run(state: InvestigationState, runtime: Runtime[InvestigationRuntime]) -> dict:
            runtime.context.tools.usage.consultations += 1
            result = await specialist_graphs[name].ainvoke({
                "packet": state["packet"], "classification": state["classification"],
                "previous_findings": state["findings"],
            }, context=runtime.context, config={"recursion_limit": 12})
            return {"candidate": result.get("finding"), "consulted": [*state["consulted"], name]}

        return run

    def validate(state: InvestigationState, runtime: Runtime[InvestigationRuntime]) -> dict:
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

    def finalize(state: InvestigationState, runtime: Runtime[InvestigationRuntime]) -> dict:
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
            and any(primary.cause_evidence_id == f"diff:{item.changed_file}" for item in context.correlations)
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
            confidence="high" if observed else "insufficient_evidence",
            evidence_ids=citations,
            unsupported_evidence_ids=list(dict.fromkeys(state["unsupported_ids"])),
            suggested_actions=[actions[state["selected"]]] if observed and state["selected"] else [],
            evidence_validated=bool(citations) and not state["unsupported_ids"],
            regression_attribution=attribution,
            limitations=sorted(tools.limitations),
        )}

    graph.add_node("analyze_logs", analyze_logs)
    graph.add_node("route_specialist", route)
    graph.add_node("validate_findings", validate)
    graph.add_node("finalize_diagnosis", finalize)
    for name in SPECIALISTS:
        graph.add_node(name, specialist_node(name))
        graph.add_edge(name, "validate_findings")
    routes = {**{name: name for name in SPECIALISTS}, "finalize_diagnosis": "finalize_diagnosis"}

    def next_node(state: InvestigationState) -> str:
        return state["active"] or "finalize_diagnosis"

    graph.add_edge(START, "analyze_logs")
    graph.add_edge("analyze_logs", "route_specialist")
    graph.add_conditional_edges("route_specialist", next_node, routes)
    graph.add_conditional_edges("validate_findings", next_node, routes)
    graph.add_edge("finalize_diagnosis", END)
    return graph.compile()


INVESTIGATION_GRAPH = build_investigation_graph()


async def execute_investigation(
    *, context: InvestigationContext, classification: FailureClassification, llm, tools: InvestigationTools | None = None,
) -> InvestigationExecution:
    tools = tools if tools is not None else InvestigationTools(context, InvestigationLimits())
    if tools.context != context:
        raise ValueError("Tools do not match the investigation context")
    tools.begin()
    state = await INVESTIGATION_GRAPH.ainvoke(
        {"context": context, "classification": classification}, context=InvestigationRuntime(llm, tools),
        config={"recursion_limit": 12},
    )
    diagnosis = state["diagnosis"]
    status = "completed" if diagnosis.evidence_validated and state["findings"] and not tools.limitations else "partial"
    return InvestigationExecution(
        diagnosis, state["selected"], state["findings"], tools.usage, tools.sources,
        list(tools.evidence), state["consulted"], status,
    )


async def run_investigation(
    *, context: InvestigationContext, classification: FailureClassification, llm, tools: InvestigationTools | None = None,
) -> DiagnosisResult:
    return (await execute_investigation(context=context, classification=classification, llm=llm, tools=tools)).diagnosis
