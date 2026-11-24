import re

from app.services.gh_collector import GitHubCollector
from app.services.github_webhook_event import RepositoryLocator

COMMENT_PAGE_LIMIT = 10


class CommentLookupIncomplete(ValueError):
    """The existing conversation could not be searched completely."""


class GitHubPullRequests:
    def __init__(self, collector: GitHubCollector, repo: RepositoryLocator, token: str):
        if repo.owner in {".", ".."} or repo.name in {".", ".."}:
            raise ValueError("Invalid repository")
        self.collector = collector
        self.path = f"/repos/{repo.owner}/{repo.name}"
        self.token = token

    async def _request(self, method: str, suffix: str, *, missing_ok: bool = False, **kwargs):
        response = await self.collector.repository_request(method, self.path + suffix, self.token, **kwargs)
        if missing_ok and response.status_code == 404:
            return None, {}
        response.raise_for_status()
        return response.json(), response.links

    async def associated_pull_requests(self, sha: str) -> list[int] | None:
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Invalid commit")
        rows, links = await self._request("GET", f"/commits/{sha}/pulls", params={"per_page": 100})
        if not isinstance(rows, list):
            raise TypeError("Invalid pull request list")
        if "next" in links:
            return None
        return list(dict.fromkeys(row["number"] for row in rows))

    async def get_pull_request(self, number: int) -> dict | None:
        data, _ = await self._request("GET", f"/pulls/{number}", missing_ok=True)
        return data

    async def find_comment(self, number: int, matches) -> dict | None:
        for page in range(1, COMMENT_PAGE_LIMIT + 1):
            rows, links = await self._request(
                "GET", f"/issues/{number}/comments", params={"per_page": 100, "page": page},
            )
            if not isinstance(rows, list):
                raise TypeError("Invalid comment list")
            for row in rows:
                if matches(row):
                    return row
            if "next" not in links:
                return None
        raise CommentLookupIncomplete("Comment scan limit reached")

    async def get_comment(self, comment_id: int) -> dict | None:
        data, _ = await self._request("GET", f"/issues/comments/{comment_id}", missing_ok=True)
        return data

    async def create_comment(self, number: int, body: str) -> dict:
        data, _ = await self._request("POST", f"/issues/{number}/comments", json={"body": body})
        return data

    async def update_comment(self, comment_id: int, body: str) -> dict:
        data, _ = await self._request("PATCH", f"/issues/comments/{comment_id}", json={"body": body})
        return data
