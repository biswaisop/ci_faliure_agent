# Repository Guidelines

## Project Structure

- `backend/app/main.py` is the FastAPI entry point. Webhook endpoints live in `backend/app/routes/`. GitHub authentication, evidence collection, delivery deduplication, and background processing live in `backend/app/services/`.
- `backend/config.py` loads settings, `backend/dependencies.py` wires services together, and `backend/tests/` contains automated tests and manual GitHub checks.
- `frontend/app/` contains Next.js App Router pages, layouts, and global styles. Static assets live in `frontend/public/`. Follow `frontend/AGENTS.md` when changing the frontend.

## Build, Test, and Development Commands

Run backend commands from `backend/` with Python 3.12 or newer.

- `uv sync` installs dependencies from the lockfile.
- `uv run uvicorn app.main:app --reload` starts the API on port 8000. Use `GET /health` to verify startup.
- `uv run pytest` runs the backend test suite. Use `uv run pytest tests/test_gh_webhook.py` for webhook changes.
- `uv run ruff check .` runs the Python linter.

Run frontend commands from `frontend/`. Use `npm ci` to install locked dependencies, `npm run dev` to serve locally on port 3000, `npm run lint` to run ESLint, and `npm run build` to verify the production build. Use `npm start` after building to serve it locally.

## Coding Style and Testing

Use four spaces and `snake_case` for Python modules and functions. Use two spaces in TypeScript and TSX, PascalCase for React components, and the App Router filenames `page.tsx` and `layout.tsx`.

Name pytest files `test_*.py` and tests `test_<behavior>`. Follow the webhook suite's FastAPI TestClient and mocked dependencies to exercise webhook behavior without live GitHub calls. The collector, run history, and commit diff scripts require GitHub access for manual verification. No coverage threshold or frontend test runner is configured.

## Configuration and Pull Requests

For local API startup, configure `GITHUB_APP_ID`, `GITHUB_PRIVATE_KEY_PATH`, and `GITHUB_WEBHOOK_SECRET`. Keep `.env` and private keys out of Git.

Follow the history's short, imperative commit subjects with prefixes such as `feat:` and `chore:`. In pull requests, summarize the change, list checks run, link any related issue, and include screenshots for visible frontend changes.
