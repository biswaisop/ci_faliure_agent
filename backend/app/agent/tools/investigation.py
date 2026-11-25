import asyncio
import json
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated

import httpx
from langchain.tools import tool
from pydantic import Field

from app.agent.investigation_router import JS_SUFFIXES
from app.schema.investigation import (
    InvestigationLimits,
    InvestigationUsage,
    Specialist,
    ToolRequest,
)
from app.schema.investigation_context import InvestigationContext
from app.services.gh_collector import (
    COMPARE_FILE_LIMIT,
    GitHubCollector,
    github_failure_reason,
)
from app.services.github_pr_resolver import COMMIT_SHA
from app.services.github_source_reader import (
    GitHubSourceReader,
    repository_path_allowed,
    source_path_allowed,
)
from app.services.github_webhook_event import RepositoryLocator
from app.services.log_reducer import strip_masked_inputs
from app.services.pr_comment_renderer import SENSITIVE_TEXT

PY_CONFIG = {"pyproject.toml", "requirements.txt", "uv.lock", "poetry.lock", "pytest.ini"}
JS_CONFIG = {"package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "tsconfig.json"}
FAILURE_CONTEXT_BYTES = 90 * 1024


class GitHubBudgetExhausted(Exception):
    pass


class InvestigationTools:
    """Per-run evidence registry with repository and workflow scoped reads."""

    def __init__(
        self, context: InvestigationContext, limits: InvestigationLimits, *,
        github: GitHubCollector | None = None, repo: RepositoryLocator | None = None,
        token: str | None = None, failed_at: datetime | None = None,
    ):
        if repo is not None and repo.github_repo_id != context.github_repo_id:
            raise ValueError("Investigation repository mismatch")
        if repo is not None and (repo.owner in {".", ".."} or repo.name in {".", ".."}):
            raise ValueError("Invalid investigation repository")
        self.context = context
        self.changed_files = {
            file.filename: file for file in context.changed_files[:COMPARE_FILE_LIMIT]
            if repository_path_allowed(file.filename)
        }
        self.limits = limits
        self.usage = InvestigationUsage()
        self.evidence: dict[str, str] = {}
        self.sources: dict[str, str] = {}
        self.limitations: set[str] = set()
        self._started = False
        self.lock = asyncio.Lock()
        self._unavailable = False
        self._attempted_sources: set[str] = set()
        self._failed_at = failed_at
        self._github = github
        self._repo = repo
        self._token = token
        self._reader = (
            GitHubSourceReader(github, repo, token, read_response=self._read_response)
            if github is not None and repo is not None and token else None
        )

    def begin(self) -> None:
        if self._started:
            raise ValueError("Investigation tools already belong to an execution")
        self._started = True

    def _charge_github(self) -> None:
        if self._unavailable or self.usage.github_calls >= self.limits.github_calls:
            raise GitHubBudgetExhausted
        self.usage.github_calls += 1

    async def _read_response(self, path, token, **kwargs):
        self._charge_github()
        async with asyncio.timeout(self.limits.github_timeout):
            return await self._github.read_repository_response(path, token, **kwargs)

    def _reply(self, *, evidence_id: str | None = None, text: str = "", limitation: str = "") -> dict:
        if evidence_id is not None:
            if SENSITIVE_TEXT.search(text):
                return self._reply(limitation="An evidence item was withheld by the content filter.")
            size = len(text.encode("utf-8")) + len(evidence_id.encode("utf-8"))
            if self.usage.output_bytes + size <= self.limits.output_bytes:
                self.usage.output_bytes += size
                self.evidence[evidence_id] = text
                return {"evidence_id": evidence_id}
            limitation = "The tool-output budget was exhausted."
        self.limitations.add(limitation)
        return {"unavailable": limitation}

    async def invoke(self, request: ToolRequest, specialist: Specialist) -> dict:
        if self.usage.tool_calls >= self.limits.tool_calls:
            return self._reply(limitation="The investigation tool-call budget was exhausted.")
        self.usage.tool_calls += 1
        try:
            return await self._invoke(request, specialist)
        except GitHubBudgetExhausted:
            return self._reply(limitation="Further GitHub reads were unavailable within this investigation.")
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError) as exc:
            if github_failure_reason(exc, comparing=False) == "github_rate_limited":
                self._unavailable = True
                return self._reply(limitation="GitHub rate limited investigation reads.")
            return self._reply(limitation="GitHub did not return usable investigation evidence.")

    async def model_result(self, request: ToolRequest, specialist: Specialist, sent: dict[str, str]) -> dict:
        async with self.lock:
            result = await self.invoke(request, specialist)
            if evidence_id := result.get("evidence_id"):
                text = self.evidence[evidence_id]
                if text != sent.get(evidence_id):
                    sent[evidence_id] = text
                    result["evidence"] = text
            return result

    async def _invoke(self, request: ToolRequest, specialist: Specialist) -> dict:
        context = self.context
        if request.name in {"get_failed_job_details", "get_job_log_excerpt"}:
            packet = next((item for item in context.failure_packets if item.job_id == request.job_id), None)
            if packet is None:
                return self._reply(limitation="The requested job is outside the failed workflow attempt.")
            if request.name == "get_failed_job_details":
                return self._reply(
                    evidence_id=f"job:{packet.job_id}",
                    text=packet.model_dump_json(exclude={"relevant_log", "failing_tests"}),
                )
            start = request.start_line - 1
            lines = packet.relevant_log.splitlines()[start:start + min(request.max_lines, self.limits.log_lines)]
            if lines:
                return self._reply(
                    evidence_id=f"log:{packet.job_id}:{start + 1}-{start + len(lines)}",
                    text=strip_masked_inputs("\n".join(lines)),
                )
            return self._reply(limitation="The requested reduced log lines are unavailable.")

        if request.name == "get_commit_diff_summary":
            return self._reply(evidence_id=f"comparison:{context.run_id}", text=json.dumps({
                "status": context.comparison_status, "reason": context.comparison_reason,
                "files": list(self.changed_files),
            }))
        if request.name == "get_changed_file_patch":
            file = self.changed_files.get(request.path)
            if file is None or not file.patch:
                return self._reply(limitation="The requested changed-file patch is unavailable.")
            evidence_id = f"diff:{file.filename}"
            existing_patch = json.loads(self.evidence.get(evidence_id, "{}")).get("patch")
            if existing_patch == file.patch:
                return {"evidence_id": evidence_id}
            return self._reply(evidence_id=evidence_id, text=file.model_dump_json())

        if request.name == "get_run_history_summary":
            evidence_id = f"history:{context.workflow_id}"
            if evidence_id in self.evidence:
                return {"evidence_id": evidence_id}
            if self._reader is None or self._failed_at is None:
                return self._reply(limitation="Workflow history is unavailable in this investigation.")
            self._charge_github()
            async with asyncio.timeout(self.limits.github_timeout):
                history = await self._github.get_run_history(
                    self._repo.owner, self._repo.name, context.workflow_id, self._token,
                    branch=context.head_branch, per_page=request.limit, created_before=self._failed_at, first_page_only=True,
                )
            return self._reply(evidence_id=evidence_id, text=json.dumps([
                {key: item.get(key) for key in ("id", "run_attempt", "conclusion", "head_sha", "created_at")}
                for item in history[:request.limit]
                if item.get("workflow_id") == context.workflow_id and item.get("id") != context.run_id
            ]))

        path = request.path or ""
        if not self._path_allowed(path, specialist, workflow=request.name == "read_workflow_file"):
            return self._reply(limitation="The source path is outside the specialist's approved scope.")
        if not COMMIT_SHA.fullmatch(context.head_sha) or self._reader is None:
            return self._reply(limitation="An authorized immutable source revision is unavailable.")
        prefix = "workflow" if path == context.workflow_path else "source"
        evidence_id = f"{prefix}:{path}@{context.head_sha}"
        if evidence_id in self.evidence:
            return {"evidence_id": evidence_id}
        if path in self._attempted_sources:
            return self._reply(limitation="The requested source file was unavailable.")
        if self.usage.source_reads >= self.limits.source_reads:
            return self._reply(limitation="The source-read budget was exhausted.")
        self.usage.source_reads += 1
        self._attempted_sources.add(path)
        files = await self._reader.read_files(context.head_sha, [path], max_file_bytes=self.limits.file_bytes)
        if path not in files:
            return self._reply(limitation="The source file was absent, oversized, or unsuitable for text analysis.")
        response = self._reply(evidence_id=evidence_id, text=files[path])
        if "evidence_id" in response:
            self.sources[path] = files[path]
        return response

    def _path_allowed(self, path: str, specialist: Specialist, *, workflow: bool) -> bool:
        if not source_path_allowed(path):
            return False
        file = PurePosixPath(path)
        if workflow or path.startswith(".github/"):
            return (
                path == self.context.workflow_path and file.parent.as_posix() == ".github/workflows"
                and file.suffix in {".yml", ".yaml"}
            )
        if path in self.changed_files:
            return True
        python = file.suffix == ".py" or file.name in PY_CONFIG
        javascript = file.suffix in JS_SUFFIXES or file.name in JS_CONFIG
        if specialist == "python":
            return python
        if specialist == "javascript":
            return javascript
        return file.name in PY_CONFIG | JS_CONFIG or (file.name.startswith("next.config.") and javascript)


def investigation_tools(registry: InvestigationTools, specialist: Specialist, visible_evidence: dict[str, str], can_read):
    sent = visible_evidence.copy()

    @tool
    async def inspect_changed_file(path: Annotated[str, Field(max_length=200)]) -> dict:
        """Inspect a path from changed_files. Return its comparison patch and source at the failed commit."""
        if not can_read():
            registry.limitations.add("The investigation model-tool budget was exhausted.")
            return {"unavailable": "Report a finding from the evidence already collected."}
        file = registry.changed_files.get(path)
        if file is None:
            return {"unavailable": "Choose a path from changed_files."}
        result = {}
        if file.patch:
            result["patch"] = await registry.model_result(
                ToolRequest(name="get_changed_file_patch", path=path), specialist, sent,
            )
        if file.status != "removed" and source_path_allowed(path):
            result["source"] = await registry.model_result(
                ToolRequest(name="read_repository_file", path=path), specialist, sent,
            )
        return result or {"unavailable": "The changed file has no patch or current source."}

    @tool(args_schema=ToolRequest)
    async def inspect_evidence(
        name: str, path: str | None = None, job_id: int | None = None,
        start_line: int = 1, max_lines: int = 50, limit: int = 5,
    ) -> dict:
        """Read a job detail/log excerpt, diff summary/patch, repository or workflow file, or workflow history."""
        if not can_read():
            registry.limitations.add("The investigation model-tool budget was exhausted.")
            return {"unavailable": "Report a finding from the evidence already collected."}
        return await registry.model_result(ToolRequest(
            name=name, path=path, job_id=job_id, start_line=start_line, max_lines=max_lines, limit=limit,
        ), specialist, sent)

    if registry.usage.tool_calls >= registry.limits.tool_calls:
        return []
    return [inspect_changed_file, inspect_evidence] if registry.changed_files else [inspect_evidence]
