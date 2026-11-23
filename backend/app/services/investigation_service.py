
import logging

from app.agent.investigation_agent import (
    run_investigation,
)
from app.db.repositories.investigation_repository import (
    InvestigationRepository,
)
from app.schema.diagnosis import DiagnosisResult
from app.schema.failure_classification import (
    FailureClassification,
)
from app.schema.investigation_context import (
    InvestigationContext,
)
from app.services.llm import get_llm

logger = logging.getLogger(__name__)


class InvestigationService:
    def __init__(
        self,
        repository: InvestigationRepository,
        llm=None,
    ):
        self.repository = repository
        self.llm = llm

    async def investigate(
        self,
        *,
        context: InvestigationContext,
        classification: FailureClassification,
    ) -> DiagnosisResult:

        try:
            await self.repository.mark_running(context)
            diagnosis = await run_investigation(
                context=context,
                classification=classification,
                llm=self.llm if self.llm is not None else get_llm(),
            )

            await self.repository.save_diagnosis(
                context,
                diagnosis,
            )

            logger.info(
                "Investigation completed: "
                "repo_id=%s run_id=%s attempt=%s "
                "confidence=%s",
                context.github_repo_id,
                context.run_id,
                context.run_attempt,
                diagnosis.confidence,
            )

            return diagnosis

        except Exception as exc:
            logger.warning(
                "Investigation failed: "
                "repo_id=%s run_id=%s attempt=%s error_type=%s",
                context.github_repo_id,
                context.run_id,
                context.run_attempt,
                type(exc).__name__,
            )

            try:
                await self.repository.mark_failed(
                    context,
                    error_code="investigation_failed",
                )
            except Exception as persistence_error:  # noqa: BLE001 - preserve the original investigation failure.
                logger.error(
                    "Could not persist investigation failure: "
                    "run_id=%s error_type=%s",
                    context.run_id,
                    type(persistence_error).__name__,
                )
            raise
