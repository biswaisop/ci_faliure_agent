# Work Done vs. Concept Specification

Comparison of the current implementation against the architecture defined in [concept.md](concept.md).

---

## 1. Summary Status Matrix

| Concept Pillar / Step | Status | Current Implementation |
| :--- | :---: | :--- |
| **GitHub App Authentication** | `Completed` | RS256 JWT generation, installation ID resolution, and access token minting (`app/services/gh_auth.py`). |
| **Webhook Ingestion & Deduplication** | `Completed` | Webhook endpoint with HMAC-SHA256 signature verification, event validation, and atomic receipt deduplication via MongoDB unique index (`app/routes/gh_webhook.py`, `app/services/deduplicator.py`). |
| **Evidence Collection** | `Completed` | `GitHubCollector` class (`app/services/gh_collector.py`) fetches failed jobs, streams job logs, computes commit diffs, and retrieves historical workflow runs. |
| **1. Reduce Before Reasoning** | `Completed` | The log reducer strips ANSI codes and runner timestamps, extracts pytest failures, Python exceptions, and standalone `Error:` lines, then keeps a bounded excerpt in each `FailurePacket`. |
| **2. Diff Correlation** | `Completed` | The collector compares the failing commit with an earlier passing commit from the same workflow and branch after verifying ancestry. The context builder correlates failing file paths with changed and renamed files. |
| **3. Failure Classification** | `Completed` | The rule-based classifier returns `code_bug`, `dependency`, `infra_config`, or `unknown`, with confidence scores and reasons. |
| **4. Bounded Investigation Agent** | `Completed` | LangGraph prepares bounded evidence, checks reported errors against cited logs, and falls back to extracted errors. The dashboard displays possible causes separately from the observed failure. |
| **5. Structured Reporting & Dashboard** | `Completed` | The dashboard shows workflow runs, diagnoses, diffs, streamed job logs, untested fix suggestions, and PR comment status. Opt-in PR commenting publishes a diagnosis to a verified originating PR and updates it when a suggestion is available. |
| **6. Sandboxed Auto-Fix** | `Planned` | Post-MVP scope per spec. |
| **7. Trust / Feedback Loop** | `Planned` | Post-MVP scope per spec (👍/👎 feedback capture and repeat failure fingerprint store). |

---

## 2. Completed Work (Detailed)

### GitHub App Auth & Security (`backend/app/services/gh_auth.py`, `app/services/gh_installations.py`)
- `verify_signature`: Verifies incoming GitHub webhook HMAC-SHA256 signatures against `GITHUB_WEBHOOK_SECRET`.
- `generate_app_jwt`: Signs short-lived RS256 JWTs using GitHub App private key (`.pem`).
- `get_installation_id`: Retrieves installation ID for a given repository.
- `get_installation_token`: Obtains repository-scoped installation tokens for API calls.
- `verify_user_installations`: Verifies user access to GitHub App installations during OAuth PKCE handshake.

### Webhook Ingestion & Deduplication (`backend/app/routes/gh_webhook.py`, `app/services/deduplicator.py`)
- `POST /github/webhook`: Ingests deliveries, verifies HMAC-SHA256 signatures, filters for completed failed `workflow_run` events, and dispatches non-blocking FastAPI `BackgroundTasks`.
- `MongoDeliveryDeduplicator`: Atomic delivery receipt recording in MongoDB (`webhook_deliveries` collection) using a unique index on `delivery_id` with majority write concern.
- `InMemoryDeliveryDeduplicator`: Asyncio lock-guarded in-memory deduplicator for test isolation.

### Evidence Collector (`backend/app/services/gh_collector.py`)
- `GitHubCollector`: Class encapsulating all GitHub REST API interactions with standardized headers and client reuse.
- `get_failed_jobs`: Fetches paginated job metadata and isolates failing jobs and steps.
- `get_job_log`: Downloads raw job log text with a rolling memory buffer (up to 2 MB) to prevent out-of-memory errors.
- `open_job_log`: Context manager streaming the complete job log from GitHub for backend proxying.
- `get_commit_diff`: Compares commit ranges to extract file changes, additions, deletions, patch snippets, and truncation flags.
- `get_run_history`: Retrieves historical runs of a workflow for flakiness and baseline pass/fail analysis.
- `get_last_passing_run`: Finds the most recent successful run on the branch created prior to the failing run.
- `resolve_commit_comparison` selects a passing baseline from the same workflow and branch, verifies that it is an ancestor of the failed commit, and records comparison status when patches are partial or no usable baseline is available.

### Step 1: Log Reducer & Failure Packet Extractor (`backend/app/services/log_reducer.py`)
- `clean_log`: RegEx stripping of ANSI color/control codes and GitHub runner timestamps.
- `extract_failing_tests`: Identifies pytest failure summaries (`FAILED tests/test_*.py::test_*`).
- `extract_error` captures named exceptions such as `ModuleNotFoundError:` and standalone `Error:` lines such as those emitted by JavaScript actions.
- `extract_relevant_log`: Extracts a bounded window (default: 150 lines) centered on the failure location.
- `reduce_job_log`: Produces a compact, strongly-typed `FailurePacket` model.

### Step 2: Diff Correlation & Context Building (`backend/app/services/investigation_context_builder.py`)
- `normalize_path`: Normalizes paths across POSIX and Windows conventions.
- `extract_failing_files`: Regex scans failure packets and test IDs for Python file references.
- `correlate_files`: Cross-references failing file paths against changed files in the commit diff, tagging matches as `direct` or `renamed`.
- `build_investigation_context`: Assembles run identifiers, failure packets, commit diffs, and file correlations into `InvestigationContext`.

### Step 3: Failure Classification Engine (`backend/app/services/failure_classifier.py`)
- `classify_failure`: Deterministic rule-based engine categorizing failures:
  - `infra_config`: Matches timeout, host resolution, runner communication, and rate limit errors.
  - `dependency`: Matches dependency resolution errors (`resolutionimpossible`, `no matching distribution`) and checks if dependency files (`requirements.txt`, `pyproject.toml`, lockfiles) changed in the commit diff.
  - `code_bug`: Matches standard Python exception types (`AssertionError`, `TypeError`, `ImportError`, etc.) and boosts confidence if diff correlations exist.
  - `unknown`: Default fallback for unrecognized patterns.

### Step 4: Bounded LangGraph Investigation Agent (`backend/app/agent/investigation_agent.py`, `app/services/investigation_service.py`)
- `build_investigation_graph`: StateGraph workflow with 3 nodes:
  1. `prepare_evidence` collects failure packet excerpts and patches for up to 15 changed files with up to 4,000 patch characters per file. It prioritizes files correlated with failures.
  2. `investigate`: Queries LLM with structured output (`DiagnosisDraft`) grounded exclusively in the prepared evidence.
  3. `validate_diagnosis` checks citation IDs and matches the model's reported error against a cited log. If the model omits or paraphrases the error, the validator uses the error extracted from a failure packet and adds a citation to that job log.
- The diagnosis displays the reported failure and presents the model's explanation under **Possible cause**. Failure confidence applies to the reported error. Saved diagnoses retain their original text until the run is processed again.
- `InvestigationService`: Coordinates `mark_running`, graph execution via `ChatNVIDIA`, and persistence via `save_diagnosis` or `mark_failed`.

### Step 5: Repositories & Investigation API (`backend/app/routes/repositories.py`)
- `GET /api/github/repositories`: Lists authorized repositories with recent run histories and statuses.
- `GET /api/github/repositories/{repo_id}/runs`: Lists recent workflow runs for a repository.
- `GET /api/github/repositories/{repo_id}/runs/{run_id}/attempts/{attempt}`: Fetches attempt metadata, failure packets, investigation diagnosis, and changed files.
- `GET /api/github/repositories/{repo_id}/runs/{run_id}/attempts/{attempt}/jobs/{job_id}/log`: Streams live/complete raw job log from GitHub to the browser.

### Step 5: Dashboard Caching (`frontend/app/dashboard/`)

- Repository pages are cached in memory per Clerk session, installation, and page while the dashboard is open. Pages are reused for up to 30 seconds, or 5 seconds when workflows are processing. Polling and Retry fetch fresh data.
- Successfully downloaded job logs are kept in memory while viewing the same workflow attempt in the same session. Reopening a job uses the cached log. Reopening after a failed or canceled download retries the request.

### PR Commenting and Fix Suggestions

- The PR resolver verifies the base repository and workflow association before publishing to an open PR. The publisher uses an attempt-specific marker, verifies the app bot author, and records its lease and write intent in MongoDB to reconcile retries.
- The fix graph reads a bounded set of files at the failed commit, checks each proposed edit against a unique source snippet, and stores suggestions separately from investigation evidence. The initial diagnosis can publish independently. A subsequent update includes a summary of the suggestion and links to the authorized dashboard for snippets.
- Both follow-ups are opt-in. See the [configuration and required GitHub permissions](README.md#optional-follow-up-operation).

### User Identity & Multi-Tenant GitHub App Linking
- Clerk webhook handling (`app/routes/clerk_webhook.py`) with out-of-order delivery protection and timestamp fencing.
- PKCE OAuth flow (`app/routes/gh_installations.py`) for connecting GitHub App installations.
- Authorization service (`app/services/github_authorization_service.py`) enforcing verified membership checks before granting repository access.

### Regression Checks and Workflow Findings

- Added regression coverage for `ModuleNotFoundError: No module named 'requests'` during pytest collection and `Error: Couldn't find issue info in current context` from the Auto Assign action.
- The Auto Assign run resolved `pozil/auto-assign-issue@v1` to a revision that reads only the issue payload. The workflow's pull request trigger supplies a pull request payload. The revision also lacks `numOfAssignee` support, which explains the separate warning. Version `v4.0.1` supports both triggers. The workflow change was verified against upstream source and remains untested in a live run. [Failing revision](https://github.com/pozil/auto-assign-issue/blob/d11e715efc663fe323c3d8d4d3cbbfdddd539baf/src/index.js#L8-L13) · [Updated PR handling](https://github.com/pozil/auto-assign-issue/blob/v4.0.1/src/action.js#L49-L68)
- The latest backend suite passed 60 tests and skipped 12 MongoDB integration tests. Ruff passed on the changed backend files. Frontend lint, tests, and the production build passed after the dashboard changes.

---

## 3. Next Immediate Steps (Build Order Alignment)

1. **Verify PR commenting in a test installation:**
   - Approve the updated GitHub App permissions and exercise diagnosis publishing, suggestion updates, and retries on a test pull request.
   - Evaluate Check Run summaries separately.
2. **Slack & Notification Integration:**
   - Dispatch alerts to linked Slack channels upon diagnosis completion.
3. **Flaky Run Evaluation:**
   - Enhance the flakiness classifier by checking historical pass/fail rates across the same workflow ID and commit branch.
4. **Sandboxed Auto-Fix (Post-MVP):**
   - Generate candidate patches for code bugs, execute pytest in an isolated container, and open a draft PR if verification passes.
