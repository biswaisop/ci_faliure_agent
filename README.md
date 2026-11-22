# AutopCI

AutopCI collects evidence from failed GitHub Actions runs so developers can start debugging with the failed jobs, relevant log excerpts, and pytest failures in one place.

- Sign in with Clerk, connect a GitHub App installation, and see the status of linked personal accounts and organizations.
- Track installation changes and repository access from signed GitHub webhooks.
- Record each failed workflow attempt, fetch its failed jobs and logs, and store structured failure packets in MongoDB. The GitHub collector also provides helpers for commit diffs and workflow history.

The landing page shows an example diagnosis. The current backend pipeline ends with stored failure evidence.

## How it works

1. The Next.js dashboard sends a Clerk session token to FastAPI. When a user links a GitHub App installation, FastAPI verifies that user's access with GitHub before saving the link.
2. GitHub sends installation, repository, and workflow events to FastAPI. The API verifies the webhook signature and records eligible deliveries in MongoDB to prevent duplicate processing.
3. For a failed workflow attempt, a background task fetches job and step metadata and downloads job logs using a GitHub App installation token.
4. The log reducer extracts evidence such as the failed step, error message, pytest test identifiers, and a bounded log excerpt. MongoDB stores the evidence alongside the run and its collection status.

| Directory | Role |
| --- | --- |
| [`frontend/`](frontend/) | Next.js App Router, React, Tailwind CSS, and Clerk sign-in and workspace pages |
| [`backend/app/routes/`](backend/app/routes/) | FastAPI endpoints for webhooks, profiles, and GitHub installation linking |
| [`backend/app/services/`](backend/app/services/) | GitHub authentication, repository sync, webhook processing, evidence collection, and log reduction |
| [`backend/app/db/`](backend/app/db/) | MongoDB connection, indexes, models, and repositories |
| [`backend/tests/`](backend/tests/) | Pytest suite and manual GitHub checks |

## Run locally

You’ll need Python 3.12 or newer, `uv`, Node.js 20.9 or newer, npm, a reachable MongoDB instance, a Clerk application, and a GitHub App. Use a publicly reachable HTTPS backend URL for webhook deliveries. For local development, an HTTPS tunnel can forward requests to port 8000.

### 1. Configure Clerk and GitHub

- In Clerk, enable your preferred sign-in methods. Create a webhook at `https://<backend-host>/webhooks/clerk` and subscribe to `user.created`, `user.updated`, and `user.deleted`. Use that endpoint’s signing secret for `CLERK_WEBHOOK_SIGNING_SECRET`. [Clerk webhook setup](https://clerk.com/docs/guides/development/webhooks/syncing)
- In your GitHub App, activate the webhook, set its URL to `https://<backend-host>/github/webhook`, and set the webhook secret. Grant read access to **Actions** and **Metadata** for workflow and repository events. Grant **Contents** read access if you use the commit diff collector. Subscribe to **Workflow run** and **Repository**. GitHub sends installation and installation repository events automatically. [GitHub webhook events](https://docs.github.com/en/webhooks/webhook-events-and-payloads)
- Set the GitHub App’s **Setup URL** and **Callback URL** to `<frontend-origin>/dashboard/github`. For local development, use `http://localhost:3000/dashboard/github`. Leave **Request user authorization (OAuth) during installation** off to enable the Setup URL. Enable **Redirect on update** if adding or removing repositories should also return users to AutopCI. Choose **Any account** if users outside the app’s owner account should be able to install it. [GitHub App registration settings](https://docs.github.com/en/apps/maintaining-github-apps/modifying-a-github-app-registration)

### 2. Start the backend

Create `backend/.env` with your values. Store the GitHub App PEM in a local secrets directory excluded from Git.

```dotenv
FRONTEND_URL=http://localhost:3000
MONGODB_URI=mongodb://localhost:27017
# Optional. Defaults to autopci.
MONGODB_DB_NAME=autopci

CLERK_SECRET_KEY=
CLERK_WEBHOOK_SIGNING_SECRET=
# Optional. Clerk JWKS public key for local session verification.
# CLERK_JWT_KEY=

GITHUB_APP_ID=
GITHUB_APP_SLUG=
GITHUB_APP_CLIENT_ID=
GITHUB_APP_CLIENT_SECRET=
GITHUB_PRIVATE_KEY_PATH=secrets/github-app.pem
GITHUB_WEBHOOK_SECRET=
```

Use the slug from `https://github.com/apps/<slug>` and the App ID, OAuth client credentials, and PEM from that same GitHub App. The PEM path must point to a file readable by the backend process.

```sh
cd backend
uv sync --locked
uv run uvicorn app.main:app --reload --port 8000
```

Check `http://localhost:8000/health` for `{"status":"ok"}`. FastAPI’s interactive API docs are at `http://localhost:8000/docs`.

### 3. Start the frontend

From the repository root, switch to `frontend/` and copy [`.env.example`](frontend/.env.example) to `.env.local`. Set `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY` from the same Clerk instance used by FastAPI. Keep `NEXT_PUBLIC_API_URL=http://localhost:8000` for local development. The terms and privacy URLs are optional.

```sh
cd frontend
npm ci
npm run dev
```

Open `http://localhost:3000` and sign in. In the dashboard, choose **Connect GitHub**, select an account and repository access, and complete **Verify installation** when GitHub returns you to AutopCI. Use **Link existing installation** if the app is already installed or an organization owner approved the installation separately. The dashboard lists linked accounts and organizations. Manage the selected repositories in GitHub.

## Deploy on Render

Create two web services from the same repository. [Render build and deploy settings](https://render.com/docs/deploys)

| Service | Root directory | Build command | Start command |
| --- | --- | --- | --- |
| FastAPI (Python) | `backend` | `uv sync --locked` | `uv run --no-sync uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Next.js (Node) | `frontend` | `npm ci && npm run build` | `npm start` |

1. Set the backend environment variables on the Python service. Use a MongoDB URI reachable from Render, set `FRONTEND_URL` to the exact public frontend origin without a trailing slash, and set the health check path to `/health`.
2. In the backend service’s **Secret Files** section, upload the GitHub App PEM as `github-app.pem`. Set `GITHUB_PRIVATE_KEY_PATH=/etc/secrets/github-app.pem` to match the uploaded filename. [Render secret files](https://render.com/docs/configure-environment-variables#secret-files)
3. Set the frontend environment variables on the Node service. Set `NEXT_PUBLIC_API_URL` to the public FastAPI origin before building. Rebuild the service after changing a `NEXT_PUBLIC_` variable. [Next.js environment variables](https://nextjs.org/docs/app/guides/environment-variables#bundling-environment-variables-for-the-browser)
4. Update the Clerk and GitHub webhook endpoints to use the public backend URL. Set both GitHub App return URLs to `https://<frontend-host>/dashboard/github`.

## API reference

| Method and path | Purpose |
| --- | --- |
| `GET /health` | API health check |
| `POST /webhooks/clerk` | Synchronize Clerk user profiles |
| `POST /github/webhook` | Accept signed GitHub App events |
| `GET /api/users/me` | Fetch the signed-in user’s profile |
| `GET /api/github/installations` | List installations linked to the signed-in user |
| `POST /api/github/installations/authorize` | Start GitHub authorization |
| `POST /api/github/installations/complete` | Verify the callback and resolve accessible installations |
| `POST /api/github/installations/link` | Link a selected installation |

## Testing

Run the backend checks from `backend/`.

```sh
uv run pytest
uv run ruff check .
```

Set `TEST_MONGO_URI` to a local MongoDB server to include persistence tests. The manual collector, commit diff, and run history scripts in `backend/tests/` require a configured GitHub App and access to the target repository.

Run the frontend checks from `frontend/`.

```sh
npm run lint
npm run build
node --test lib/api.test.mjs
```

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| `401` from FastAPI after sign-in | Match `FRONTEND_URL` to the browser’s frontend origin and confirm both services use the same Clerk instance. |
| GitHub rejects `redirect_uri` | Register the full frontend URL ending in `/dashboard/github` under **Callback URL** for the app identified by `GITHUB_APP_CLIENT_ID`. |
| GitHub returns to organization settings | Check **Setup URL** and **Redirect on update**. Use **Link existing installation** to connect an installation that already exists. |
| `502` while loading installations | Check the backend logs, confirm the PEM file exists at `GITHUB_PRIVATE_KEY_PATH`, and verify the PEM and `GITHUB_APP_ID` belong to the same GitHub App. |
| Profile sync stays pending | Check whether Clerk delivered `user.created` to `/webhooks/clerk`. Replay the delivery after correcting the endpoint or signing secret. |

See the [frontend guide](frontend/README.md) for more on Clerk and the dashboard, and [AGENTS.md](AGENTS.md) for development conventions.

## License

[MIT](LICENSE)
