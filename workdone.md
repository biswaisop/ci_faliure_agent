# Work Done vs. Concept Specification

Comparison of the current implementation against the architecture defined in [concept.md](concept.md).

---

## 1. Summary Status Matrix

| Concept Pillar / Step | Status | Current Implementation |
| :--- | :---: | :--- |
| **GitHub App Authentication** | `Completed` | RS256 JWT generation, installation ID resolution, and access token minting (`app/services/gh_auth.py`). |
| **Webhook Ingestion & Deduplication** | `Completed` | Webhook endpoint with HMAC-SHA256 signature verification, event validation, and atomic receipt deduplication via MongoDB unique index (`app/routes/gh_webhook.py`, `app/services/deduplicator.py`). |
| **Evidence Collection** | `Completed` | `GitHubCollector` class (`app/services/gh_collector.py`) fetches failed jobs, streams job logs, computes commit diffs, and retrieves historical workflow runs. |
| **1. Reduce Before Reasoning** | `Completed` | Deterministic log preprocessor (`app/services/log_reducer.py`): ANSI stripping, runner timestamp removal, pytest failure summary parsing, Python exception extraction, and bounded window log slicing into a compact `FailurePacket`. |
| **2. Diff Correlation** | `Completed` | Baseline passing run discovery (`get_last_passing_run`), commit diff retrieval (`get_commit_diff`), and path normalizer cross-referencing failing test files against changed/renamed files (`app/services/investigation_context_builder.py`). |
| **3. Failure Classification** | `Completed` | Deterministic rule-based failure classifier (`app/services/failure_classifier.py`): classifies failures into `code_bug`, `dependency`, `infra_config`, or `possible_flaky` with confidence scores and reasoning. |
| **4. Bounded Investigation Agent** | `Completed` | LangGraph-based state graph agent loop (`app/agent/investigation_agent.py`, `app/services/investigation_service.py`, `app/services/llm.py`): prepares bounded evidence, queries LLM with structured output (`DiagnosisDraft`), and validates evidence citations before producing a `DiagnosisResult`. |
| **5. Structured Reporting & Dashboard** | `In Progress` | Frontend Next.js dashboard displaying repositories, workflow runs, classification tags, interactive diagnosis, evidence tabs, and streaming raw logs (`app/routes/repositories.py`, `frontend/app/dashboard/`). Automated PR commenting is next. |
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

### Step 1: Log Reducer & Failure Packet Extractor (`backend/app/services/log_reducer.py`)
- `clean_log`: RegEx stripping of ANSI color/control codes and GitHub runner timestamps.
- `extract_failing_tests`: Identifies pytest failure summaries (`FAILED tests/test_*.py::test_*`).
- `extract_error`: Scans for Python exception patterns (`ErrorType: message`).
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
  1. `prepare_evidence`: Binds failure packet excerpts and changed file patches into a bounded evidence dictionary.
  2. `investigate`: Queries LLM with structured output (`DiagnosisDraft`) grounded exclusively in the prepared evidence.
  3. `validate_diagnosis`: Verifies that every cited evidence ID exists in the supplied evidence map; strips hallucinated IDs and validates regression attribution.
- `InvestigationService`: Coordinates `mark_running`, graph execution via `ChatNVIDIA`, and persistence via `save_diagnosis` or `mark_failed`.

### Step 5: Repositories & Investigation API (`backend/app/routes/repositories.py`)
- `GET /api/github/repositories`: Lists authorized repositories with recent run histories and statuses.
- `GET /api/github/repositories/{repo_id}/runs`: Lists recent workflow runs for a repository.
- `GET /api/github/repositories/{repo_id}/runs/{run_id}/attempts/{attempt}`: Fetches attempt metadata, failure packets, investigation diagnosis, and changed files.
- `GET /api/github/repositories/{repo_id}/runs/{run_id}/attempts/{attempt}/jobs/{job_id}/log`: Streams live/complete raw job log from GitHub to the browser.

### User Identity & Multi-Tenant GitHub App Linking
- Clerk webhook handling (`app/routes/clerk_webhook.py`) with out-of-order delivery protection and timestamp fencing.
- PKCE OAuth flow (`app/routes/gh_installations.py`) for connecting GitHub App installations.
- Authorization service (`app/services/github_authorization_service.py`) enforcing verified membership checks before granting repository access.

---

## 3. Next Immediate Steps (Build Order Alignment)

1. **Automated PR Commenting & GitHub Check Run Summaries:**
   - Render a formatted Markdown comment from `DiagnosisResult` (summary, root cause, evidence citations, suggested actions).
   - Post directly to the originating PR or GitHub Check Run using the installation access token.
2. **Slack & Notification Integration:**
   - Dispatch alerts to linked Slack channels upon diagnosis completion.
3. **Flaky Run Evaluation:**
   - Enhance the flakiness classifier by checking historical pass/fail rates across the same workflow ID and commit branch.
4. **Sandboxed Auto-Fix (Post-MVP):**
   - Generate candidate patches for code bugs, execute pytest in an isolated container, and open a draft PR if verification passes.
