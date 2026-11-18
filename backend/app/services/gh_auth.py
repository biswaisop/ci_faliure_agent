import hmac
import hashlib
import json

from config import settings

def verify_signature(payload: bytes, signature: str) -> bool:
    """
    signs the payload and returns a boolean value that if the payload signed with the webhook is similar with the signature
    """
    if not signature or not settings.GITHUB_WEBHOOK_SECRET:
        return False

    whs = settings.GITHUB_WEBHOOK_SECRET

    expected = "sha256="+hmac.new(
        whs.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected, signature)