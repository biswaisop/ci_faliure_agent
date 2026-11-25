
import logging

from app.agent.investigation_agent import (
    execute_investigation,
)
from app.agent.investigation_tools import InvestigationTools
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
from app.services.llm import LLM_MODEL, get_llm
from config import settings

logger = logging.getLogger(__name__)


class InvestigationService:
    def __init__(
        self,
        repository: InvestigationRepository,
        llm=None,
        *,
        tools: InvestigationTools | None = None,
    ):
        self.repository = repository
        self.llm = llm
        self.tools = tools

    async def investigate(
        self,
        *,
        context: InvestigationContext,
        classification: FailureClassification,
    ) -> DiagnosisResult:

        try:
            await self.repository.mark_running(context)
            llm = self.llm if self.llm is not None else get_llm()
            execution = await execute_investigation(
                context=context,
                classification=classification,
                llm=llm,
                tools=self.tools or InvestigationTools(context, settings.AUTO_PCI_INVESTIGATION_LIMITS),
            )
            diagnosis = execution.diagnosis

            await self.repository.save_diagnosis(
                context,
                diagnosis,
                execution=execution,
                model=str(getattr(llm, "model", None) or LLM_MODEL),
                retain_sources=settings.AUTO_PCI_FIX_SUGGESTIONS,
            )

            logger.info(
                "Investigation completed: "
                "repo_id=%s run_id=%s attempt=%s "
                "confidence=%s specialist=%s execution=%s usage=%s",
                context.github_repo_id,
                context.run_id,
                context.run_attempt,
                diagnosis.confidence,
                execution.specialist,
                execution.status,
                execution.usage.model_dump(),
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
