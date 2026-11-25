from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

INVESTIGATION_VERSION = "agent-harness-v1"
Specialist = Literal["python", "javascript", "github_actions"]
Confidence = Literal["high", "medium", "low", "insufficient_evidence"]
EvidenceID = Annotated[str, Field(max_length=300)]
FindingText = Annotated[str, Field(max_length=1000)]


class InvestigationLimits(BaseModel):
    llm_routing: bool = False
    consultations: int = Field(default=2, ge=1, le=2)
    specialist_turns: int = Field(default=4, ge=1, le=4)
    tool_calls: int = Field(default=12, ge=0, le=12)
    github_calls: int = Field(default=12, ge=0, le=12)
    source_reads: int = Field(default=6, ge=0, le=6)
    file_bytes: int = Field(default=20 * 1024, ge=1, le=24 * 1024)
    log_lines: int = Field(default=200, ge=1, le=200)
    output_bytes: int = Field(default=100 * 1024, ge=1, le=100 * 1024)
    model_timeout: float = Field(default=30, gt=0, le=60)
    github_timeout: float = Field(default=10, gt=0, le=15)


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal[
        "get_failed_job_details", "get_job_log_excerpt", "get_commit_diff_summary", "get_changed_file_patch",
        "read_repository_file", "read_workflow_file", "get_run_history_summary",
    ]
    path: str | None = Field(default=None, max_length=200)
    job_id: int | None = Field(default=None, gt=0)
    start_line: int = Field(default=1, ge=1, le=200)
    max_lines: int = Field(default=50, ge=1, le=200)
    limit: int = Field(default=5, ge=1, le=10)


class SpecialistFinding(BaseModel):
    specialist: Specialist
    observed_failure: FindingText | None = None
    likely_root_cause: FindingText | None = None
    suspected_file: Annotated[str, Field(max_length=200)] | None
    confidence: Confidence
    evidence_ids: list[EvidenceID] = Field(default_factory=list, max_length=20)
    investigated_files: list[EvidenceID] = Field(default_factory=list, max_length=6)
    hypotheses: list[FindingText] = Field(default_factory=list, max_length=3)
    suggested_actions: list[FindingText] = Field(default_factory=list, max_length=3)
    needs_second_opinion: bool = False
    suggested_specialist: Specialist | None = None
    # When proposing a cause, cite a literal supporting excerpt independently of the observed error.
    cause_evidence_id: EvidenceID | None = None
    cause_quote: FindingText | None = None


class ContextSelection(BaseModel):
    relevant_diffs: list[EvidenceID] = Field(default_factory=list, max_length=10)


class PreparedContext(BaseModel):
    evidence: dict[str, str] = Field(default_factory=dict)
    relevant_diffs: list[EvidenceID] = Field(default_factory=list, max_length=10)


class RoutingDecision(BaseModel):
    specialist: Specialist | None = None
    evidence_quote: FindingText | None = None


class InvestigationUsage(BaseModel):
    consultations: int = 0
    model_turns: int = 0
    context_model_turns: int = 0
    routing_turns: int = 0
    tool_calls: int = 0
    github_calls: int = 0
    source_reads: int = 0
    output_bytes: int = 0
