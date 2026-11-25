from dataclasses import dataclass
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.agent.context_agent import prepare_context, run_context_agent
from app.agent.fix_suggestion_agent import insufficient_evidence, run_fix_suggestion
from app.agent.investigation_agent import (
    InvestigationExecution,
    run_investigation_agent,
)
from app.agent.tools.investigation import InvestigationTools
from app.schema.diagnosis import DiagnosisResult
from app.schema.failure_classification import FailureClassification
from app.schema.fix_suggestion import FixSuggestion
from app.schema.investigation import InvestigationLimits, PreparedContext
from app.schema.investigation_context import InvestigationContext


class HarnessState(TypedDict, total=False):
    raw_context: InvestigationContext
    classification: FailureClassification
    context: PreparedContext
    investigation_report: DiagnosisResult
    execution: InvestigationExecution
    fix: FixSuggestion


@dataclass(frozen=True)
class HarnessRuntime:
    llm: object
    registry: InvestigationTools
    reader: object | None
    cached_sources: dict[str, str]


async def collect_context(state: HarnessState, runtime: Runtime[HarnessRuntime]) -> dict:
    context = await run_context_agent(
        state["raw_context"], state["classification"], runtime.context.llm, runtime.context.registry,
    )
    return {"context": context}


async def investigate(state: HarnessState, runtime: Runtime[HarnessRuntime]) -> dict:
    execution = await run_investigation_agent(
        context=state["raw_context"], classification=state["classification"], llm=runtime.context.llm,
        tools=runtime.context.registry, prepared=state["context"],
    )
    context = state["context"]
    if execution.relevant_diffs != context.relevant_diffs:
        context = prepare_context(
            state["raw_context"], state["classification"], relevant_diffs=execution.relevant_diffs,
        )
    return {"context": context, "execution": execution, "investigation_report": execution.diagnosis}


async def suggest_fix(state: HarnessState, runtime: Runtime[HarnessRuntime]) -> dict:
    if not state["investigation_report"].evidence_validated:
        return {"fix": insufficient_evidence()}
    execution = state.get("execution")
    cached_sources = runtime.context.cached_sources if execution is None else execution.sources
    suggestion = await run_fix_suggestion(
        context=state["raw_context"], classification=state["classification"], llm=runtime.context.llm,
        diagnosis=state["investigation_report"], reader=runtime.context.reader,
        prepared=state["context"], cached_sources=cached_sources,
    )
    return {"fix": suggestion}


def build_harness():
    graph = StateGraph(HarnessState, context_schema=HarnessRuntime)
    graph.add_node("context_agent", collect_context)
    graph.add_node("investigation_agent", investigate)
    graph.add_node("fixing_agent", suggest_fix)
    graph.add_conditional_edges(START, lambda state: "fixing_agent" if "investigation_report" in state else "context_agent", {
        "fixing_agent": "fixing_agent", "context_agent": "context_agent",
    })
    graph.add_edge("context_agent", "investigation_agent")
    graph.add_conditional_edges(
        "investigation_agent",
        lambda state, runtime: "fixing_agent" if runtime.context.reader is not None and state["investigation_report"].evidence_validated else END,
        {"fixing_agent": "fixing_agent", END: END},
    )
    graph.add_edge("fixing_agent", END)
    return graph.compile()


HARNESS = build_harness()


async def run_harness(
    *, context: InvestigationContext, classification: FailureClassification, llm,
    tools: InvestigationTools | None = None, reader=None, diagnosis: DiagnosisResult | None = None,
    relevant_diffs: list[str] | None = None, cached_sources: dict[str, str] | None = None,
) -> HarnessState:
    registry = tools if tools is not None else InvestigationTools(context, InvestigationLimits())
    if registry.context != context:
        raise ValueError("Tools do not match the investigation context")
    state: HarnessState = {"raw_context": context, "classification": classification}
    if diagnosis is not None:
        if reader is None:
            raise ValueError("A source reader is required to resume the fixing agent")
        state["investigation_report"] = diagnosis
        state["context"] = prepare_context(context, classification, relevant_diffs=relevant_diffs)
    else:
        registry.begin()
    return await HARNESS.ainvoke(state, context=HarnessRuntime(llm, registry, reader, cached_sources or {}))
