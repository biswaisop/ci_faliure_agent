import httpx
from pymongo.errors import PyMongoError

from app.db.models.webhook_delivery import ProcessingError


class WebhookProcessingError(Exception):
    def __init__(self, code: ProcessingError) -> None:
        super().__init__(code)
        self.code = code


def processing_error(exc: Exception) -> ProcessingError:
    """Classify failures for storage and logs without copying sensitive exception messages."""
    if isinstance(exc, WebhookProcessingError):
        return exc.code
    if isinstance(exc, httpx.TimeoutException):
        return "github_timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        headers = exc.response.headers
        if status == 429 or (status == 403 and (
            headers.get("x-ratelimit-remaining") == "0" or "retry-after" in headers
        )):
            return "github_rate_limit"
        if status in (401, 403, 404, 410):
            return "github_access_unavailable"
        return "github_api_error"
    if isinstance(exc, httpx.RequestError):
        return "github_api_error"
    if isinstance(exc, PyMongoError):
        return "database_error"
    if isinstance(exc, (KeyError, TypeError, ValueError)):
        return "invalid_github_data"
    return "processing_error"
