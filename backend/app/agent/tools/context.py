from typing import Annotated

from langchain.tools import tool
from pydantic import Field

from app.agent.tools.investigation import InvestigationTools
from app.schema.investigation import ToolRequest


def context_tools(registry: InvestigationTools):
    sent = registry.evidence.copy()
    remaining = min(2, registry.limits.tool_calls - 2)

    @tool
    async def get_changed_file_patch(path: Annotated[str, Field(max_length=200)]) -> dict:
        """Read the retained commit-comparison patch for a changed file in this workflow attempt."""
        nonlocal remaining
        if remaining <= 0 or registry.usage.context_model_turns >= 2:
            return {"unavailable": "Return the context selection from the patches already available."}
        remaining -= 1
        return await registry.model_result(
            ToolRequest(name="get_changed_file_patch", path=path), "github_actions", sent,
        )

    return [get_changed_file_patch] if remaining > 0 else []
