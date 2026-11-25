
from typing import Literal

from pydantic import BaseModel, Field

Runtime = Literal["python", "javascript", "typescript", "nodejs", "nextjs", "github_action", "runner", "unknown"]
FailureOrigin = Literal[
    "application_code", "test_execution", "dependency_installation", "build_tool",
    "third_party_action", "github_platform", "runner_environment", "unknown",
]


class FailurePacket(BaseModel):
    job_id: int
    job_name: str
    failed_step: str | None = None
    action: str | None = None
    runtime: Runtime = "unknown"
    failure_origin: FailureOrigin = "unknown"
    warnings: list[str] = Field(default_factory=list)

    error_type: str | None = None
    error_message: str | None = None
    error_signature: str | None = None

    failing_tests: list[str] = Field(default_factory=list)

    relevant_log: str = ""
    exit_code: int | None = None

    extraction_status: str = "no_failure_found"
