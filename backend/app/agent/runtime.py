import asyncio
import json

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, wrap_model_call
from langchain.agents.structured_output import ToolStrategy
from pydantic import BaseModel


async def invoke_agent[Output: BaseModel](
    *, name: str, model, prompt: str, payload: dict, tools: list, output: type[Output],
    turns: int, timeout: float, on_model_call=None,
) -> Output | None:
    @wrap_model_call
    async def bound_model(request, handler):
        if on_model_call is not None:
            on_model_call()
        async with asyncio.timeout(timeout):
            return await handler(request)

    agent = create_agent(
        model=model, name=name, system_prompt=prompt, tools=tools, response_format=ToolStrategy(output),
        middleware=[ModelCallLimitMiddleware(run_limit=turns), bound_model],
    )
    result = await agent.ainvoke(
        {"messages": [("human", json.dumps(payload, ensure_ascii=False))]},
        config={"recursion_limit": 5 * turns + 5},
    )
    return result.get("structured_response")
