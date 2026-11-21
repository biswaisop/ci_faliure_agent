from pymongo import ASCENDING

from app.db.mongodb import MongoDb


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
    await MongoDb.get_collection("github_installation_states").create_index(
        "expires_at", expireAfterSeconds=0,
    )

    # GitHub repositories
    await MongoDb.github_repositories().create_index(
        "github_repo_id",
        unique=True
    )

    # Webhook deduplication
    await MongoDb.webhook_deliveries().create_index(
        "delivery_id",
        unique=True
    )

    # Workflow runs: separate rerun attempts
    await MongoDb.workflow_runs().create_index(
        [
            ("github_repo_id", ASCENDING),
            ("run_id", ASCENDING),
            ("run_attempt", ASCENDING),
        ],
        unique=True
    )

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
    await MongoDb.investigations().create_index(
        [
            ("github_repo_id", ASCENDING),
            ("run_id", ASCENDING),
            ("run_attempt", ASCENDING),
        ]
    )

    await MongoDb.agent_executions().create_index(
        "investigation_id"
    )
