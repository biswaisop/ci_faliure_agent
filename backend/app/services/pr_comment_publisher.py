import asyncio
import hashlib
import logging
import re
from datetime import UTC, datetime, timedelta

import httpx
from pymongo.errors import PyMongoError

from app.db.repositories.pr_comment_repository import PRCommentRepository
from app.db.repositories.user_repositories import now_utc
from app.services.github_pr_client import CommentLookupIncomplete
from app.services.github_pr_resolver import resolve_pull_request
from app.services.pr_comment_renderer import comment_marker, render_pr_comment

logger = logging.getLogger(__name__)


def github_failure(exc: Exception) -> tuple[str, int | None]:
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        headers = response.headers
        status = response.status_code
        if status == 429 or (status == 403 and (
            "retry-after" in headers or headers.get("x-ratelimit-remaining") == "0"
            or "rate limit" in response.text[:1000].lower()
        )):
            retry = headers.get("retry-after", "")
            reset = headers.get("x-ratelimit-reset", "")
            delay = int(retry) if retry.isascii() and retry.isdecimal() and len(retry) <= 12 else 60
            if headers.get("x-ratelimit-remaining") == "0" and reset.isascii() and reset.isdecimal() and len(reset) <= 12:
                delay = max(delay, int(reset) - int(datetime.now(UTC).timestamp()))
            return "rate_limited", max(delay, 60) if delay <= 86400 else None
        if status == 401:
            return "unauthorized_installation", 60
        if status == 403:
            return "missing_permission", None
        if status in {404, 410}:
            return "pr_unavailable", None
        if status < 500 and status != 408:
            return "github_rejected", None
    if isinstance(exc, CommentLookupIncomplete):
        return "comment_scan_limit", None
    if isinstance(exc, (httpx.HTTPError, TimeoutError)):
        return "github_unavailable", 60
    if isinstance(exc, PyMongoError):
        return "publication_storage_unavailable", 60
    return "publication_failed", None


class PRCommentPublisher:
    def __init__(self, publications: PRCommentRepository, app_slug: str, app_id: str, dashboard_origin: str):
        self.publications = publications
        self.app_slug = app_slug
        self.app_id = app_id
        self.dashboard_origin = dashboard_origin

    async def publish(self, run, repo, github, diagnosis, classification, context, fix=None) -> int | None:
        row = await self.publications.claim(run)
        if row is None:
            return 60
        lease = row["lease_id"]
        uncertain = row.get("create_uncertain", False)
        try:
            async with asyncio.timeout(110):
                if not re.fullmatch(r"[a-z0-9-]+", self.app_slug):
                    await self.publications.finish(run, lease, "failed", error_code="app_identity_missing")
                    return None
                retry_at = row.get("retry_at")
                if retry_at and (remaining := (retry_at.replace(tzinfo=UTC) - now_utc()).total_seconds()) > 0:
                    await self.publications.finish(run, lease, row.get("retry_status", "failed"))
                    return int(remaining) + 1
                pr = row.get("pr_number")
                if pr is None:
                    resolution = await resolve_pull_request(run, repo, github)
                    pr, status = resolution.number, resolution.status
                else:
                    target = await github.get_pull_request(pr)
                    base = (target or {}).get("base") or {}
                    if not target or target.get("state") != "open" or target.get("number") != pr or (
                        (base.get("repo") or {}).get("id") != repo.github_repo_id
                    ):
                        pr = None
                    status = "no_pr_found"
                if pr is None:
                    await self.publications.finish(run, lease, status, error_code=None)
                    return None

                marker = comment_marker(run, pr)
                issue_path = f"{github.path}/issues/{pr}".lower()

                def matches(comment: dict) -> bool:
                    author = comment.get("user") or {}
                    app = comment.get("performed_via_github_app")
                    issue_url = str(comment.get("issue_url", "")).lower()
                    return (
                        author.get("type") == "Bot"
                        and author.get("login") == f"{self.app_slug}[bot]"
                        and (app is None or str(app.get("id")) == self.app_id)
                        and issue_url == f"https://api.github.com{issue_path}"
                        and str(comment.get("body", "")).splitlines()[:1] == [marker]
                        and type(comment.get("id")) is int and comment["id"] > 0
                    )

                body = render_pr_comment(
                    repo, run, pr, diagnosis, classification, context, self.dashboard_origin, fix,
                )
                digest = hashlib.sha256(body.encode()).hexdigest()
                previous_id = row.get("comment_id")
                comment = await github.get_comment(previous_id) if previous_id else None
                if not comment or not matches(comment):
                    comment = await github.find_comment(pr, matches)

                published_at = now_utc()
                if comment:
                    status = "published"
                    if comment["body"] != body:
                        await github.update_comment(comment["id"], body)
                        status = "updated"
                    elif row.get("content_hash") == digest and row.get("comment_id") == comment["id"]:
                        status = row.get("last_success_status", "published")
                        published_at = row.get("last_published_at") or published_at
                    comment_id = comment["id"]
                elif uncertain:
                    # Reconcile this intent before allowing any further remote write.
                    await self.publications.finish(run, lease, "uncertain", error_code="create_unconfirmed")
                    return 60
                else:
                    await self.publications.write(run, lease, pr_number=pr, create_uncertain=True)
                    uncertain = True
                    try:
                        created = await github.create_comment(pr, body)
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code in {400, 401, 403, 404, 410, 422, 429}:
                            uncertain = False
                        raise
                    if not matches(created):
                        raise ValueError("Could not verify created comment")
                    comment_id = created["id"]
                    status = "published"

                await self.publications.finish(
                    run, lease, status, pr_number=pr, comment_id=comment_id, content_hash=digest,
                    create_uncertain=False, error_code=None, last_success_status=status, last_published_at=published_at,
                    retry_at=None,
                )
                return None
        except Exception as exc:  # noqa: BLE001 - isolate optional publication and keep its checkpoint.
            code, retry = github_failure(exc)
            logger.warning("PR publication status run_id=%s reason=%s", run.run_id, code)
            status = "uncertain" if uncertain else "failed"
            await self.publications.finish(
                run, lease, status, error_code=code, create_uncertain=uncertain, retry_status=status,
                    retry_at=now_utc() + timedelta(seconds=retry) if code == "rate_limited" and retry else None,
            )
            return (retry or 60) if uncertain else retry
