
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.schema.diagnosis import DiagnosisResult
from app.schema.investigation import INVESTIGATION_VERSION

if TYPE_CHECKING:
    from app.agent.investigation_agent import InvestigationExecution


def now_utc() -> datetime:
    return datetime.now(UTC)


class InvestigationRepository:
    def __init__(self, collection):
        self.collection = collection

    @staticmethod
    def _key(context) -> dict:
        return {
            "github_repo_id": context.github_repo_id,
            "run_id": context.run_id,
            "run_attempt": context.run_attempt,
        }

    async def get(self, run, *, include_fix_sources: bool = False) -> dict | None:
        projection = {"status": 1, "diagnosis": 1}
        if include_fix_sources:
            projection["fix_sources"] = 1
        return await self.collection.find_one(self._key(run), projection)

    async def list_statuses(self, github_repo_id: int, runs) -> dict:
        if not runs:
            return {}
        query = {
            "github_repo_id": github_repo_id,
            "$or": [{"run_id": run.run_id, "run_attempt": run.run_attempt} for run in runs],
        }
        projection = {"run_id": 1, "run_attempt": 1, "status": 1}
        return {
            (row["run_id"], row["run_attempt"]): row["status"]
            async for row in self.collection.find(query, projection)
        }

    async def mark_pending(self, run) -> None:
        now = now_utc()
        await self.collection.update_one(
            self._key(run),
            {"$setOnInsert": {"status": "pending", "created_at": now, "updated_at": now}},
            upsert=True,
        )

    async def mark_running(self, context) -> None:
        await self.collection.update_one(
            self._key(context),
            {
                "$set": {
                    "status": "running",
                    "error_code": None,
                    "diagnosis": None,
                    "execution_status": "running",
                    "started_at": now_utc(),
                    "updated_at": now_utc(),
                },
                "$unset": {"fix_sources": "", "investigation_metadata": "", "completed_at": ""},
                "$setOnInsert": {
                    "created_at": now_utc(),
                },
            },
            upsert=True,
        )

    async def save_diagnosis(
        self,
        context,
        diagnosis: DiagnosisResult,
        *,
        execution: InvestigationExecution | None = None,
        model: str | None = None,
        retain_sources: bool = False,
    ) -> None:
        fields = {}
        if execution is not None:
            fields = {
                "execution_status": execution.status,
                "investigation_metadata": {
                    "version": INVESTIGATION_VERSION, "model": model, "selected_specialist": execution.specialist,
                    "consulted_specialists": execution.consulted, "evidence_ids": execution.evidence_ids,
                    "findings": [item.model_dump(mode="json") for item in execution.findings],
                    "usage": execution.usage.model_dump(mode="json"),
                },
            }
            if retain_sources and execution.sources:
                fields["fix_sources"] = {
                    "sha": context.head_sha,
                    "files": [{"path": path, "text": text} for path, text in execution.sources.items()],
                }
        result = await self.collection.update_one(
            self._key(context),
            {
                "$set": {
                    "status": "completed",
                    "diagnosis": diagnosis.model_dump(
                        mode="json"
                    ),
                    "error_code": None,
                    "updated_at": now_utc(),
                    "completed_at": now_utc(),
                    **fields,
                }
            },
        )

        if result.matched_count != 1:
            raise RuntimeError(
                "Investigation record was not found"
            )

    async def mark_failed(
        self,
        context,
        error_code: str,
    ) -> None:
        await self.collection.update_one(
            self._key(context),
            {
                "$set": {
                    "status": "failed",
                    "execution_status": "failed",
                    "error_code": error_code,
                    "updated_at": now_utc(),
                }
            },
        )
