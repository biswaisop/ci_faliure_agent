from fastapi import status
from inspect import signature
import json
from fastapi import APIRouter, Request, HTTPException
from app.services.gh_auth import verify_signature

router = APIRouter(prefix="/github", tags=["webhook"])

@router.post("/webhook")
async def github_webhooks(request: Request):
    """Handle incoming GitHub webhook events and detect failed CI workflow runs."""
    payload = await request.body()

    print("WEBHOOK RECEIVED!")
    print("EVENT:", request.headers.get("X-GitHub-Event"))
    print("PAYLOAD:", payload.decode()[:1000])


    signature = request.headers.get(
        "X-Hub-Signature-256"
    )


    if not verify_signature(payload, signature):
        raise HTTPException(
            status_code=401,
            detail="Invalid webhook signature"
        )
    event  = request.headers.get("X-Github-Event")
    if event != "workflow_run":
        return {"status":"ignored"}
    data = json.loads(payload)

    run = data["workflow_run"]

    if run.get("conclusion") != "failure":
        return {"status":"ignored"}

    if event == "ping":
        print("GitHub App webhook connected!")
        return {"status": "pong"}

    print("FAILED CI RUN DETECTED!")
    print("Repository:", data["repository"]["full_name"])
    print("Workflow:", run["name"])
    print("Run ID:", run["id"])
    print("Commit:", run["head_sha"])


    return {
        "status": "failure_detected",
        "repository": data["repository"]["full_name"],
        "run_id": run["id"],
        "commit": run["head_sha"]
    }