from typing import Annotated

from langchain.tools import tool
from pydantic import Field

from app.services.github_source_reader import MAX_SOURCE_BYTES
from app.services.pr_comment_renderer import SENSITIVE_TEXT


async def load_fix_sources(reader, sha: str, candidates: list[str], cached: dict[str, str]) -> dict[str, str]:
    sources = {}
    remaining = MAX_SOURCE_BYTES
    pending = [path for path in candidates if path not in cached]
    available = {**cached, **(await reader.read_files(sha, pending) if pending else {})}
    for path in candidates:
        source = available.get(path, "")
        size = len(source.encode("utf-8"))
        if 0 < size <= remaining and not SENSITIVE_TEXT.search(source):
            sources[path] = source
            remaining -= size
    return sources


def fixing_tools(sources: dict[str, str]):
    unread = set(sources)

    @tool
    async def read_fix_source(path: Annotated[str, Field(max_length=200)]) -> dict:
        """Read a candidate source file already retrieved from the failed commit."""
        if path not in unread:
            return {"unavailable": "This source is unavailable or already in the conversation."}
        unread.remove(path)
        return {"evidence_id": f"source:{path}", "text": sources[path]}

    return [read_fix_source]
