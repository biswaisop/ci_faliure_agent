import asyncio
import json
import logging

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    wrap_model_call,
    wrap_tool_call,
)
from langchain.agents.structured_output import ToolStrategy
from pydantic import BaseModel

from config import settings

logger = logging.getLogger("uvicorn.error.agents")


async def invoke_agent[Output: BaseModel](
    *, name: str, model, prompt: str, payload: dict, tools: list, output: type[Output],
    turns: int, timeout: float, on_model_call=None,
) -> Output | None:
    verbose = settings.AUTO_PCI_AGENT_VERBOSE
    turn = 0
    paths = payload.get("changed_files") or payload.get("changed_paths") or payload.get("available_sources") or []

    @wrap_model_call
    async def bound_model(request, handler):
        nonlocal turn
        turn += 1
        if verbose:
            logger.info("[%s] Model turn %d/%d", name, turn, turns)
        if on_model_call is not None:
            on_model_call()
        async with asyncio.timeout(timeout):
            return await handler(request)

    @wrap_tool_call
    async def log_tool(request, handler):
        tool_name = request.tool.name if request.tool else "unknown"
        path = request.tool_call["args"].get("path")
        label = f"{tool_name} path={path}" if path in paths else tool_name
        logger.info("[%s] Tool started: %s", name, label)
        result = await handler(request)
        logger.info("[%s] Tool returned: %s", name, label)
        return result

    middleware = [ModelCallLimitMiddleware(run_limit=turns), bound_model]
    if verbose:
        middleware.append(log_tool)
        logger.info("[%s] Started", name)
    agent = create_agent(
        model=model, name=name, system_prompt=prompt, tools=tools, response_format=ToolStrategy(output),
        middleware=middleware,
    )
    try:
        result = await agent.ainvoke(
            {"messages": [("human", json.dumps(payload, ensure_ascii=False))]},
            config={"recursion_limit": 5 * turns + 5},
        )
    except Exception as exc:
        if verbose:
            logger.info("[%s] Failed with %s", name, type(exc).__name__)
        raise
    response = result.get("structured_response")
    if verbose:
        logger.info("[%s] Finished structured_response=%s", name, response is not None)
    return response
