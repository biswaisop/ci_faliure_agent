import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent.harness import run_harness
from app.agent.tools.investigation import InvestigationTools
from app.schema.fix_suggestion import FixSuggestion, SuggestedFileChange
from app.schema.investigation import ContextSelection, InvestigationLimits
from app.schema.investigation_context import ChangedFile
from app.services.failure_classifier import classify_failure
from app.services.log_reducer import reduce_job_log
from config import settings
from tests.test_investigation_agent import PAGES_LOG, FakeLLM, finding, make_context

pytestmark = pytest.mark.anyio
CONFIG = "next.config.mjs"
EXPORT = "export default function config(phase) {"
ORIGINAL = f"{EXPORT}\n  return nextConfig;\n}}"
SOURCE = f'const nextConfig = {{ output: "export" }};\n{ORIGINAL}\n'


async def test_investigator_chooses_a_changed_file_outside_the_context_selection():
    config = '''import { PHASE_DEVELOPMENT_SERVER } from "next/constants.js";

/** @type {import('next').NextConfig} */
const isProd = process.env.NODE_ENV === "production";
// Set basePath for GitHub Pages deployment
const basePath = process.env.NEXT_PUBLIC_BASE_PATH !== undefined
  ? process.env.NEXT_PUBLIC_BASE_PATH
  : (isProd ? "/portFolio" : "");

const nextConfig = {
  output: "export",
  basePath: basePath,
  images: {
    unoptimized: true,
  },
  trailingSlash: true,
};

export default function config(phase) {
  return {
    ...nextConfig,
    distDir: phase === PHASE_DEVELOPMENT_SERVER ? ".next-dev" : ".next",
  };
}
'''
    # Match the action's input header, config output, error and cleanup sequence from the reported run.
    lines = [
        "Cache restored successfully", "##[group]Run actions/configure-pages@v5", "with:",
        "  static_site_generator: next", "  token: ***", "  enablement: false", "##[endgroup]",
        "Injecting property=output and value=export in:", *config.splitlines(),
        "##[error]TypeError: error must be an instance of Error", "Post job cleanup.",
        "[command]/usr/bin/git version", "##[warning]Node.js 20 is deprecated.",
    ]
    raw = "\n".join(f"2026-11-25T17:17:37.{150000 + index}Z {line}" for index, line in enumerate(lines))
    packet = reduce_job_log(raw, 123, "build", "Setup Pages")
    diff_id = f"diff:{CONFIG}"
    context = make_context(
        packet, comparison_status="available", diff_available=True,
        changed_files=[
            ChangedFile(filename="app.ts", status="modified", patch="+const unrelated = true;"),
            ChangedFile(filename=CONFIG, status="modified", patch=f"-export default nextConfig;\n+{EXPORT}"),
            ChangedFile(filename="docs/design notes.md", status="modified"),
        ],
    )
    tools = InvestigationTools(context, InvestigationLimits())
    cause = "The Pages action likely could not inject the export setting after the config changed to a function export."
    model = FakeLLM(
        ContextSelection(relevant_diffs=["diff:app.ts"]),
        finding(
            "github_actions", packet, likely_root_cause=cause, confidence="medium", suspected_file=CONFIG,
            observed_failure="GitHub Pages stopped during configuration.",
            cause_evidence_id="log:123", cause_quote=EXPORT,
        ),
    )
    state = await run_harness(context=context, classification=classify_failure(context), llm=model, tools=tools)
    payload = model.prompts[1][1]
    assert payload["changed_files"] == ["app.ts", CONFIG, "docs/design notes.md"]
    assert set(payload["evidence"]) == {"log:123"}
    log = json.loads(payload["evidence"]["log:123"])["relevant_log"]
    assert "Injecting property=output and value=export" in log and EXPORT in log
    assert "token:" not in log and "Post job cleanup" not in log and "Cache restored" not in log
    report = state["investigation_report"]
    assert report.possible_cause == cause and report.suspected_file == CONFIG
    assert report.regression_attribution == "insufficient_evidence"
    assert report.evidence_ids == ["log:123"]
    assert state["context"].relevant_diffs == [diff_id]
    assert tools.usage.model_turns == 1 and tools.usage.tool_calls == 0


@pytest.mark.parametrize("resume", [False, True])
async def test_pages_context_and_report_reach_fixer(resume, caplog, monkeypatch):
    monkeypatch.setattr(settings, "AUTO_PCI_AGENT_VERBOSE", True)
    caplog.set_level("INFO", logger="uvicorn.error.agents")
    packet = reduce_job_log(PAGES_LOG.replace("##[error]", f"{SOURCE}\n##[error]"), 123, "Deploy", "Setup Pages")
    diff_id = f"diff:{CONFIG}"
    context = make_context(
        packet, comparison_status="available", diff_available=True,
        changed_files=[
            ChangedFile(filename="app.ts", status="modified", patch="+const unrelated = true;"),
            ChangedFile(filename=CONFIG, status="modified", patch=f"-export default nextConfig;\n+{EXPORT}"),
        ],
    )
    classification = classify_failure(context)
    proposal = FixSuggestion(
        status="suggested", summary="Export the configuration object", rationale="The action rewrites an object export.",
        changes=[SuggestedFileChange(
            path=CONFIG, original_snippet=ORIGINAL, replacement_snippet="export default nextConfig;",
            explanation="Expose the already configured static export object to the action.",
        )],
        evidence_ids=["log:123", diff_id, f"source:{CONFIG}"],
        tests_to_run=["Rerun the Pages workflow"],
    )
    model = FakeLLM(
        [("get_changed_file_patch", {"path": CONFIG})],
        ContextSelection(relevant_diffs=["diff:invented.ts", diff_id]),
        finding(
            "github_actions", packet, suspected_file=CONFIG,
            likely_root_cause="The Pages action likely cannot rewrite the new function export.",
            confidence="medium", cause_evidence_id="log:123", cause_quote=EXPORT,
        ),
        [("read_fix_source", {"path": CONFIG})],
        proposal,
    )
    reader = SimpleNamespace(read_files=AsyncMock(return_value={CONFIG: SOURCE}))
    state = await run_harness(context=context, classification=classification, llm=model, reader=None if resume else reader)
    report = state["investigation_report"]
    assert report.suspected_file == CONFIG and report.possible_cause
    assert report.regression_attribution == "insufficient_evidence"
    assert state["context"].relevant_diffs == [diff_id]
    if resume:
        assert "fix" not in state
        reader.read_files.assert_not_awaited()
        state = await run_harness(
            context=context, classification=classification, llm=model, reader=reader, diagnosis=report,
            relevant_diffs=state["execution"].relevant_diffs,
        )
    assert state["fix"] == proposal and state["fix"].verified is False
    assert set(model.prompts[2][1]["evidence"]) == {"log:123"}
    assert model.prompts[3][1]["context"] == state["context"].evidence
    assert model.prompts[3][1]["investigation_report"] == report.model_dump()
    assert {"evidence_id": diff_id} in model.transcripts[1]
    assert {"evidence_id": f"source:{CONFIG}", "text": SOURCE} in model.transcripts[4]
    assert reader.read_files.await_args.args[0] == context.head_sha
    assert reader.read_files.await_args.args[1][0] == CONFIG
    assert "[context_agent] Tool started: get_changed_file_patch" in caplog.text
    assert "[investigation_agent] Model turn 1/1" in caplog.text
    assert f"[fixing_agent] Tool returned: read_fix_source path={CONFIG}" in caplog.text
    assert "[fixing_agent] Finished structured_response=True" in caplog.text
    assert EXPORT not in caplog.text


async def test_logged_change_can_reach_fixer_when_cause_is_unconfirmed():
    packet = reduce_job_log(PAGES_LOG.replace("##[error]", f"{EXPORT}\n##[error]"), 123, "Deploy", "Setup Pages")
    diff_id = f"diff:{CONFIG}"
    context = make_context(
        packet, changed_files=[ChangedFile(filename=CONFIG, status="modified", patch=f"+{EXPORT}")],
    )
    model = FakeLLM(
        ContextSelection(relevant_diffs=[diff_id]),
        finding("github_actions", packet, suspected_file=CONFIG),
        FixSuggestion(status="insufficient_evidence", summary="No verified edit", rationale="Cause remains unknown."),
    )
    reader = SimpleNamespace(read_files=AsyncMock(return_value={CONFIG: SOURCE}))
    state = await run_harness(context=context, classification=classify_failure(context), llm=model, reader=reader)
    assert state["investigation_report"].possible_cause is None
    assert state["investigation_report"].suspected_file == CONFIG
    assert state["fix"].status == "insufficient_evidence"
    assert model.prompts[2][1]["investigation_report"]["suspected_file"] == CONFIG
