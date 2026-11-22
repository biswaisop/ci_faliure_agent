from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import AliasChoices, BaseModel, Field, ValidationError

from app.schema.github_installation import GitHubAccount, GitHubId
from app.schema.webhook_delivery import DeliveryMetadata
from app.schema.workflow_run import WorkflowRunMetadata

GitHubEvent = Literal["workflow_run", "installation", "installation_repositories", "repository"]
GitHubPathSegment = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_.-]{1,100}$")]

EVENT_ACTIONS = {
    "workflow_run": {"completed"},
    "installation": {"created", "deleted", "suspend", "unsuspend", "new_permissions_accepted"},
    "installation_repositories": {"added", "removed"},
    "repository": {"created", "renamed", "transferred", "deleted", "edited", "privatized", "publicized"},
}


class RepositoryLocator(BaseModel):
    owner: GitHubPathSegment
    name: GitHubPathSegment
    github_repo_id: GitHubId

    @classmethod
    def from_payload(cls, payload: dict) -> "RepositoryLocator":
        owner = payload.get("owner")
        login = owner.get("login") if isinstance(owner, dict) else None
        name = payload.get("name")
        full_name = payload.get("full_name")
        if (not login or not name) and isinstance(full_name, str) and "/" in full_name:
            login, name = full_name.split("/", 1)
        if login in (".", "..") or name in (".", ".."):
            raise ValueError("Invalid repository path")
        return cls(owner=login, name=name, github_repo_id=payload.get("id"))


class InstallationRef(BaseModel):
    installation_id: GitHubId = Field(validation_alias=AliasChoices("id", "installation_id"))


@dataclass(frozen=True)
class GitHubWebhook:
    metadata: DeliveryMetadata
    repository: RepositoryLocator | None = None
    run: WorkflowRunMetadata | None = None


def parse_webhook(event: GitHubEvent, payload: dict) -> GitHubWebhook | None:
    """Filter and validate only the signed event fields needed by the background process."""
    action = payload.get("action")
    if not isinstance(action, str) or action not in EVENT_ACTIONS[event]:
        return None

    run = payload.get("workflow_run")
    if event == "workflow_run" and (not isinstance(run, dict) or run.get("conclusion") != "failure"):
        return None

    installation = payload.get("installation")
    installation_id = (
        InstallationRef.model_validate(installation).installation_id if installation is not None else None
    )
    if event != "workflow_run" and installation_id is None:
        raise ValueError("Missing installation information")
    if event == "installation" and action == "deleted":
        # GitHub signs the body only. A repository deletion has a bare installation reference.
        GitHubAccount.model_validate(installation.get("account"))
    if event == "installation_repositories" and not all(
        isinstance(payload.get(field), list) for field in ("repositories_added", "repositories_removed")
    ):
        raise ValueError("Missing installation repository change information")

    locator = None
    if event in ("workflow_run", "repository"):
        repository = payload.get("repository")
        if not isinstance(repository, dict):
            raise ValueError("Missing repository information")
        locator = RepositoryLocator.from_payload(repository)

    workflow_run = None
    if locator is not None and isinstance(run, dict) and event == "workflow_run":
        try:
            workflow_run = WorkflowRunMetadata(
                github_repo_id=locator.github_repo_id,
                installation_id=installation_id,
                run_id=run.get("id"),
                run_attempt=run.get("run_attempt", 1),
                workflow_id=run.get("workflow_id"),
                run_number=run.get("run_number"),
                head_sha=run.get("head_sha"),
                head_branch=run.get("head_branch"),
                event=run.get("event"),
                status=run.get("status", "completed"),
                conclusion=run["conclusion"],
                html_url=run.get("html_url"),
                github_created_at=run.get("created_at"),
                github_updated_at=run.get("updated_at"),
            )
        except ValidationError as exc:
            raise ValueError("Missing or invalid workflow run identifiers") from exc

    return GitHubWebhook(
        metadata=DeliveryMetadata(
            event_type=event, action=action, installation_id=installation_id,
            github_repo_id=locator.github_repo_id if locator else None,
            run_id=workflow_run.run_id if workflow_run else None,
            run_attempt=workflow_run.run_attempt if workflow_run else None,
        ),
        repository=locator,
        run=workflow_run,
    )
