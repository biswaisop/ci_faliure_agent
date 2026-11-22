"""Dependencies shared by the backend's GitHub collectors."""

from app.services.gh_collector import GitHubCollector

_github_collector = GitHubCollector()


def get_github_collector() -> GitHubCollector:
    """Return the shared GitHub API collector."""
    return _github_collector
