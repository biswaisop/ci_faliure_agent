from pymongo import ASCENDING, DESCENDING, IndexModel

from app.db.mongodb import MongoDb
from app.db.repositories.github_repository import FULL_NAME_COLLATION


async def setup_indexes() -> None:
    # Users
    await MongoDb.users().create_index(
        "clerk_user_id",
        unique=True
    )

    await MongoDb.users().create_index(
        "status",
    )

    # GitHub installations
    await MongoDb.github_installations().create_index(
        "installation_id",
        unique=True
    )
    await MongoDb.github_installations().create_index("clerk_user_ids")
    await MongoDb.github_installations().create_indexes([
        IndexModel("account.github_account_id"),
        IndexModel("status"),
    ])
    await MongoDb.get_collection("github_installation_states").create_index(
        "expires_at", expireAfterSeconds=0,
    )

    # GitHub repositories
    await MongoDb.github_repositories().create_index(
        "github_repo_id",
        unique=True
    )
    await MongoDb.github_repositories().create_indexes([
        IndexModel([("installation_id", ASCENDING), ("is_active", ASCENDING)]),
        IndexModel("full_name", collation=FULL_NAME_COLLATION),
    ])

    await MongoDb.get_collection("user_memberships").create_indexes([
        IndexModel(
            [("clerk_user_id", ASCENDING), ("resource_type", ASCENDING), ("resource_id", ASCENDING)],
            unique=True,
        ),
        IndexModel([("clerk_user_id", ASCENDING), ("status", ASCENDING)]),
        IndexModel([("resource_type", ASCENDING), ("resource_id", ASCENDING), ("status", ASCENDING)]),
    ])

    # Webhook deduplication
    await MongoDb.webhook_deliveries().create_index(
        "delivery_id",
        unique=True
    )
    await MongoDb.webhook_deliveries().create_indexes([
        IndexModel([("status", ASCENDING), ("received_at", ASCENDING)]),
        IndexModel([("run_id", ASCENDING), ("run_attempt", ASCENDING)]),
    ])

    # Workflow runs: separate rerun attempts
    await MongoDb.workflow_runs().create_index(
        [
            ("github_repo_id", ASCENDING),
            ("run_id", ASCENDING),
            ("run_attempt", ASCENDING),
        ],
        unique=True
    )
    await MongoDb.workflow_runs().create_indexes([
        IndexModel([("github_repo_id", ASCENDING), ("created_at", DESCENDING)]),
        IndexModel([
            ("github_repo_id", ASCENDING), ("workflow_id", ASCENDING),
            ("head_branch", ASCENDING), ("created_at", DESCENDING),
        ]),
        IndexModel("conclusion"),
    ])

    # Slack
    await MongoDb.slack_installations().create_index(
        "team_id",
        unique=True
    )

    await MongoDb.slack_threads().create_index(
        [
            ("team_id", ASCENDING),
            ("channel_id", ASCENDING),
            ("thread_ts", ASCENDING),
        ],
        unique=True
    )

    # Investigations
    await MongoDb.agent_executions().create_index(
        "investigation_id"
    )

    investigations = MongoDb.investigations()
    index_name = "github_repo_id_1_run_id_1_run_attempt_1"
    existing = (await investigations.index_information()).get(index_name)
    if existing and not existing.get("unique"):
        index_name = "unique_investigation_attempt"
    await investigations.create_index(
        [("github_repo_id", ASCENDING), ("run_id", ASCENDING), ("run_attempt", ASCENDING)],
        name=index_name,
        unique=True,
    )
