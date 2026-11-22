from bson import BSON
from pymongo import DESCENDING
from pymongo.errors import DuplicateKeyError

from app.db.models.workflow_run import (
    CollectionStatus,
    EvidenceUpdate,
    FailedJob,
    WorkflowRun,
    WorkflowRunKey,
    WorkflowRunMetadata,
)
from app.db.mongodb import MongoDb
from app.db.repositories.user_repositories import now_utc

MAX_FAILED_JOBS_BYTES = 2 * 1024 * 1024


class WorkflowRunInstallationConflict(ValueError):
    """A run attempt already belongs to a different installation."""


class WorkflowRunRepository:
    def __init__(self, collection=None):
        self.collection = collection if collection is not None else MongoDb.workflow_runs()

    @staticmethod
    def _key(run: WorkflowRunKey) -> dict:
        return {"github_repo_id": run.github_repo_id, "run_id": run.run_id, "run_attempt": run.run_attempt}

    async def upsert_workflow_run(self, run: WorkflowRunMetadata) -> None:
        """Store metadata by attempt without clearing collected jobs or evidence."""
        now = now_utc()
        key = self._key(run)
        if run.installation_id is not None:
            key["$or"] = [{"installation_id": run.installation_id}, {"installation_id": None}]
        update = {
            "$set": {**run.model_dump(exclude_none=True), "updated_at": now},
            "$setOnInsert": {
                "created_at": now, "failed_jobs": [], "evidence": {"collection_status": "pending"},
            },
        }
        try:
            await self.collection.update_one(key, update, upsert=True)
        except DuplicateKeyError:
            result = await self.collection.update_one(key, update)
            if not result.matched_count:
                raise WorkflowRunInstallationConflict from None

    async def get_run(self, run: WorkflowRunKey) -> WorkflowRun | None:
        """Read one attempt of a workflow run."""
        document = await self.collection.find_one(self._key(run))
        return WorkflowRun.model_validate(document) if document else None

    async def list_runs_by_repository(
        self, github_repo_id: int, *, installation_id: int | None = None, limit: int = 50,
    ) -> list[WorkflowRun]:
        """Read recent recorded attempts, optionally scoped to their original installation."""
        query = {"github_repo_id": github_repo_id}
        if installation_id is not None:
            query["installation_id"] = installation_id
        return await self._recent(query, limit)

    async def get_recent_failures(
        self, github_repo_id: int, *, workflow_id: int | None = None,
        head_branch: str | None = None, installation_id: int | None = None, limit: int = 50,
    ) -> list[WorkflowRun]:
        """Read recent failures, optionally filtered by workflow and branch."""
        query = {"github_repo_id": github_repo_id, "conclusion": "failure"}
        if workflow_id is not None:
            query["workflow_id"] = workflow_id
        if head_branch is not None:
            query["head_branch"] = head_branch
        if installation_id is not None:
            query["installation_id"] = installation_id
        return await self._recent(query, limit)

    async def _recent(self, query: dict, limit: int) -> list[WorkflowRun]:
        if limit < 1:
            raise ValueError("limit must be positive")
        cursor = self.collection.find(query).sort("created_at", DESCENDING).limit(limit)
        return [WorkflowRun.model_validate(document) async for document in cursor]

    async def add_failed_jobs(self, run: WorkflowRunKey, jobs: list[FailedJob]) -> None:
        """Merge jobs by ID, preserving earlier metadata and enforcing the BSON budget."""
        key = self._key(run)
        while True:
            document = await self.collection.find_one(key, {"failed_jobs": 1, "failed_jobs_version": 1})
            if document is None:
                raise ValueError("Workflow run does not exist")
            existing = document.get("failed_jobs", [])
            merged = {job["job_id"]: job for job in existing}
            for job in jobs:
                previous = merged.get(job.job_id, {})
                fields = job.model_dump(exclude_unset=True, exclude_none=True)
                if previous.get("log_status") not in (None, "not_collected") and fields.get("log_status") == "not_collected":
                    fields.pop("log_status")
                steps = {step["number"]: step for step in previous.get("failed_steps", [])}
                steps.update((step.number, step.model_dump()) for step in job.failed_steps)
                merged[job.job_id] = FailedJob.model_validate({
                    **previous, **fields, "failed_steps": [steps[number] for number in sorted(steps)],
                }).model_dump()

            updated_jobs = list(merged.values())
            if updated_jobs == existing:
                return
            if len(BSON.encode({"failed_jobs": updated_jobs})) > MAX_FAILED_JOBS_BYTES:
                raise ValueError("Failed job metadata exceeds the workflow run storage budget")

            result = await self.collection.update_one(
                {**key, "failed_jobs_version": document.get("failed_jobs_version")},
                {"$set": {"failed_jobs": updated_jobs, "updated_at": now_utc()}, "$inc": {"failed_jobs_version": 1}},
            )
            if result.matched_count:
                return

    async def update_evidence(self, run: WorkflowRunKey, evidence: EvidenceUpdate) -> None:
        """Merge available compact evidence fields without clearing earlier values."""
        fields = {f"evidence.{key}": value for key, value in evidence.model_dump(exclude_none=True).items()}
        if fields:
            await self.collection.update_one(
                self._key(run), {"$set": {**fields, "updated_at": now_utc()}},
            )

    async def update_collection_status(self, run: WorkflowRunKey, status: CollectionStatus) -> None:
        """Track collection progress while preserving a completed attempt across redeliveries."""
        query = self._key(run)
        if status != "completed":
            query["evidence.collection_status"] = {"$ne": "completed"}
        await self.collection.update_one(
            query, {"$set": {"evidence.collection_status": status, "updated_at": now_utc()}},
        )
