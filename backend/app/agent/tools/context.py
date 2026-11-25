from typing import Annotated

from langchain.tools import tool
from pydantic import Field

from app.agent.tools.investigation import InvestigationTools
from app.schema.investigation import ToolRequest


def context_tools(registry: InvestigationTools):
    sent = registry.evidence.copy()

    @tool
    async def get_changed_file_patch(path: Annotated[str, Field(max_length=200)]) -> dict:
        """Read the retained commit-comparison patch for a changed file in this workflow attempt."""
        if registry.usage.context_model_turns >= 2:
            return {"unavailable": "Return the context selection from the patches already available."}
        return await registry.model_result(
            ToolRequest(name="get_changed_file_patch", path=path), "github_actions", sent,
        )

    return [get_changed_file_patch] if registry.usage.tool_calls < registry.limits.tool_calls else []
