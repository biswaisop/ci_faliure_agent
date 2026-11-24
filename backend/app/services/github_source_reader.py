import base64
import binascii
import json
import re
from pathlib import PurePosixPath

from app.services.gh_collector import GitHubCollector
from app.services.github_pr_resolver import COMMIT_SHA
from app.services.github_webhook_event import RepositoryLocator
from app.services.pr_comment_renderer import SENSITIVE_TEXT

MAX_FILES = 6
MAX_FILE_BYTES = 24 * 1024
MAX_SOURCE_BYTES = 64 * 1024
MAX_TREE_BYTES = 1024 * 1024
MANIFESTS = ("requirements.txt", "pyproject.toml", "package.json", "Gemfile", "go.mod", "Cargo.toml")
TEXT_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rb", ".rs", ".cs", ".c", ".cpp", ".h",
    ".toml", ".json", ".yaml", ".yml", ".ini", ".cfg", ".txt",
}
SENSITIVE_PATH = re.compile(
    r"(?:^|[/. _-])(?:secret(?:s)?|credential(?:s)?|password(?:s)?|private[_-]?key|id_rsa|id_ed25519)"
    r"(?:[/. _-]|$)", re.IGNORECASE,
)


def source_path_allowed(path: str) -> bool:
    return (
        re.fullmatch(r"[a-zA-Z0-9_.@/-]{1,200}", path) is not None
        and all(part and part != ".." and (not part.startswith(".") or part == ".github") for part in path.split("/"))
        and not SENSITIVE_PATH.search(path)
        and (PurePosixPath(path).suffix.lower() in TEXT_SUFFIXES or path in MANIFESTS)
    )


class GitHubSourceReader:
    def __init__(self, collector: GitHubCollector, repo: RepositoryLocator, token: str):
        if repo.owner in {".", ".."} or repo.name in {".", ".."}:
            raise ValueError("Invalid repository")
        self.collector = collector
        self.path = f"/repos/{repo.owner}/{repo.name}/git"
        self.token = token

    async def read_files(self, sha: str, paths: list[str]) -> dict[str, str]:
        candidates = list(dict.fromkeys(path for path in paths if source_path_allowed(path)))[:MAX_FILES]
        if not candidates or not COMMIT_SHA.fullmatch(sha):
            return {}
        commit = await self.collector.read_repository_response(
            f"{self.path}/commits/{sha}", self.token, limit=16384,
        )
        if commit is None:
            return {}
        metadata = json.loads(commit)
        tree_sha = (metadata.get("tree") or {}).get("sha", "")
        if metadata.get("sha") != sha or not COMMIT_SHA.fullmatch(tree_sha):
            return {}
        tree = await self.collector.read_repository_response(
            f"{self.path}/trees/{tree_sha}", self.token, limit=MAX_TREE_BYTES, params={"recursive": "1"},
        )
        if tree is None:
            return {}
        entries = {item["path"]: item for item in json.loads(tree)["tree"] if item.get("path") in candidates}
        files = {}
        remaining = MAX_SOURCE_BYTES
        for path in candidates:
            entry = entries.get(path, {})
            size, blob_sha = entry.get("size"), entry.get("sha", "")
            if (
                entry.get("type") != "blob" or entry.get("mode") not in {"100644", "100755"}
                or type(size) is not int or not 0 < size <= min(MAX_FILE_BYTES, remaining)
                or not COMMIT_SHA.fullmatch(blob_sha)
            ):
                continue
            remaining -= size
            blob = await self.collector.read_repository_response(
                f"{self.path}/blobs/{blob_sha}", self.token, limit=size * 2 + 2048,
            )
            if blob is None:
                continue
            decoded = json.loads(blob)
            if decoded.get("encoding") != "base64" or decoded.get("sha") != blob_sha:
                continue
            try:
                raw = base64.b64decode("".join(decoded["content"].split()), validate=True)
                if len(raw) != size or b"\x00" in raw:
                    continue
                source = raw.decode("utf-8")
            except (binascii.Error, UnicodeDecodeError, KeyError):
                continue
            if not SENSITIVE_TEXT.search(source):
                files[path] = source
        return files
