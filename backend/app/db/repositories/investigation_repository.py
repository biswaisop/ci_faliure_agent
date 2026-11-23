
from datetime import datetime, timezone

from app.schema.diagnosis import DiagnosisResult


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


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
