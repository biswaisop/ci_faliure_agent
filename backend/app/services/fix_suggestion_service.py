import asyncio
import hashlib
import logging

import httpx
from pydantic import ValidationError

from app.agent.fix_suggestion_agent import run_fix_suggestion
from app.db.repositories.fix_suggestion_repository import FixSuggestionRepository
from app.schema.fix_suggestion import FixSuggestion
from app.services.llm import LLM_MODEL, get_llm
from app.services.pr_comment_publisher import github_failure

logger = logging.getLogger(__name__)


def suggestion_version(context, classification, diagnosis) -> str:
    digest = hashlib.sha256(f"fix-v1:{LLM_MODEL}".encode())
    # Keep existing suggestion and comment lookups stable after adding routing metadata.
    digest.update(context.model_dump_json(exclude={
        "workflow_path": True,
        "failure_packets": {"__all__": {"runtime", "failure_origin", "action", "warnings"}},
    }).encode())
    for item in (classification, diagnosis):
        digest.update(item.model_dump_json().encode())
    return digest.hexdigest()


class FixSuggestionService:
    def __init__(self, repository: FixSuggestionRepository):
        self.repository = repository

    async def generate(
        self, run, context, classification, diagnosis, reader, *, cached_sources: dict[str, str] | None = None,
    ) -> tuple[FixSuggestion | None, int | None]:
        version = suggestion_version(context, classification, diagnosis)
        record = await self.repository.get(run, version)
        if record and record.get("suggestion"):
            return FixSuggestion.model_validate(record["suggestion"]), None
        if record and record.get("status") == "failed" and not record.get("retryable"):
            return None, None
        claim = await self.repository.claim(run, version, LLM_MODEL)
        if claim is None:
            return None, 60
        try:
            async with asyncio.timeout(90):
                suggestion = await run_fix_suggestion(
                    diagnosis=diagnosis, classification=classification, context=context, reader=reader, llm=get_llm(),
                    cached_sources=cached_sources,
                )
        except ValidationError:
            await self.repository.finish(claim, status="failed", error_code="invalid_model_output", retryable=False)
            return None, None
        except Exception as exc:  # noqa: BLE001 - persist failure independently of the investigation.
            if isinstance(exc, httpx.HTTPStatusError):
                code, retry = github_failure(exc)
            else:
                code, retry = "suggestion_unavailable", 60
            logger.warning("Fix suggestion failed run_id=%s reason=%s", run.run_id, code)
            await self.repository.finish(claim, status="failed", error_code=code, retryable=retry is not None)
            return None, retry
        await self.repository.finish(
            claim, status=suggestion.status, suggestion=suggestion.model_dump(mode="json"),
            verified=False, error_code=None, retryable=False,
        )
        return suggestion, None
