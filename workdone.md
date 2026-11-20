# Work Done vs. Concept Specification

Comparison of the current implementation against the architecture defined in [concept.md](file:///d:/big%20projects/ci_agent/backend/concept.md).

---

## 1. Summary Status Matrix

| Concept Pillar / Step | Status | Current Implementation |
| :--- | :---: | :--- |
| **GitHub App Authentication** | `Completed` | RS256 JWT generation, installation ID resolution, and access token minting (`app/services/gh_auth.py`). |
| **Webhook Ingestion** | `In Progress` | Webhook endpoint with HMAC-SHA256 signature verification and `workflow_run` failure filter (`app/routes/gh_webhook.py`). Integration with collector is pending. |
| **Evidence Collection** | `Mostly Done` | GitHub API client (`app/services/gh_collector.py`) fetches failed jobs, raw job logs, commit diffs, and workflow run history. |
| **1. Reduce Before Reasoning** | `Pending` | Deterministic log preprocessing (ANSI stripping, timestamp stripping, traceback/pytest parser, compact failure packet) not yet implemented. |
| **2. Diff Correlation** | `Pending` | Correlating failing files/tests against changed files in commit diff not yet implemented. |
| **3. Classification** | `Pending` | Routing heuristic (Flaky vs. Infra/Config vs. Dependency vs. Code Bug) not yet implemented. |
| **4. Bounded Investigation Agent** | `Pending` | LangChain / LangGraph agent loop with tool access and citations not yet implemented. |
| **5. Structured Reporting** | `Pending` | Markdown PR comment / check-run summary generator and poster not yet implemented. |
| **6. Sandboxed Auto-Fix** | `Planned` | Post-MVP scope per spec. |
| **7. Trust / Feedback Loop** | `Planned` | Post-MVP scope per spec. |

---

## 2. Completed Work (Detailed)

### GitHub App Auth & Security (`backend/app/services/gh_auth.py`)
- `verify_signature`: Verifies incoming GitHub webhook HMAC-SHA256 signatures against `GITHUB_WEBHOOK_SECRET`.
- `generate_app_jwt`: Signs short-lived RS256 JWTs using GitHub App private key (`.pem`).
- `get_installation_id`: Retrieves installation ID for a given repository.
- `get_installation_token`: Obtains repository-scoped installation tokens for API calls.

### API Gateway & Webhook Router (`backend/app/routes/gh_webhook.py`, `backend/app/main.py`)
- Basic FastAPI setup with CORS and `/health` monitoring.
- `POST /github/webhook`: Validates webhook signatures and filters for failed `workflow_run` events.

### Evidence Collector (`backend/app/services/gh_collector.py`)
- `get_failed_jobs`: Fetches job metadata and filters for failed jobs.
- `get_job_log`: Downloads raw job log text with redirect handling.
- `get_commit_diff`: Compares commit ranges to extract file changes, additions, deletions, and patch snippets.
- `get_run_history`: Retrieves the last N runs of a workflow for flakiness/regression analysis.

### Integration Tests (`backend/tests/`)
- `test_gh_collector.py`: Validates end-to-end token minting, failed job retrieval, and log extraction.
- `test_commit_diff.py`: Verifies commit diff extraction on live GitHub repositories.
- `test_run_history.py`: Verifies workflow run history retrieval.

---

## 3. Next Immediate Steps (Build Order Alignment)

To complete **Step 1 & Step 2** of the build order:

1. **Log Reducer & Failure Packet Extractor** (`app/services/log_reducer.py`):
   - Strip ANSI escape codes and runner timestamps from raw logs.
   - Implement regex/parsers for Python `pytest` failure summaries and tracebacks.
   - Structure into a compact `FailurePacket` (exit code, failing test names, error snippet).
2. **Webhook Orchestration**:
   - Connect `gh_webhook.py` to trigger the collector and reducer upon failure events.
3. **Flaky & Diff Correlation**:
   - Cross-reference `FailurePacket` files against `get_commit_diff` results.
   - Check `get_run_history` to evaluate prior pass/fail state.
4. **LLM Diagnosis & PR Commenting**:
   - Construct prompt using compact packet + diff.
   - Post diagnosis comment back to GitHub PR.
