
from datetime import UTC, datetime

from app.schema.diagnosis import DiagnosisResult


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

    async def get(self, run) -> dict | None:
        return await self.collection.find_one(self._key(run), {"status": 1, "diagnosis": 1})

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
                    "updated_at": now_utc(),
                },
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
    ) -> None:
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
                    "error_code": error_code,
                    "updated_at": now_utc(),
                }
            },
        )
