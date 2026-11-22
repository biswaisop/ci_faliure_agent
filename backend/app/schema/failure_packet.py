
from pydantic import BaseModel, Field


class FailurePacket(BaseModel):
    job_id: int
    job_name: str
    failed_step: str | None = None

    error_type: str | None = None
    error_message: str | None = None
    error_signature: str | None = None

    failing_tests: list[str] = Field(default_factory=list)

    relevant_log: str = ""
    exit_code: int | None = None

    extraction_status: str = "no_failure_found"
