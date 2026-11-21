# CI Postmortem Agent frontend

Copy `.env.example` to `.env.local` in this directory. Add the publishable and secret keys for the same Clerk instance. Set `NEXT_PUBLIC_API_URL` to the FastAPI origin and set `NEXT_PUBLIC_TERMS_URL` and `NEXT_PUBLIC_PRIVACY_URL` to your published legal pages. Keep `CLERK_SECRET_KEY` server-side.

```sh
npm ci
npm run dev
```

Open `http://localhost:3000/sign-in`. Clerk's sign-in and sign-up components own GitHub OAuth, email verification, password reset, and sessions. They route completed sign-ins and sign-ups to `/dashboard` by default. The proxy sets up Clerk and redirects signed-out dashboard requests to `/sign-in`. The dashboard also checks the session on the server before rendering.

## Clerk Dashboard

1. In **User & authentication**, enable email for sign-up and sign-in, require verification at sign-up, and enable passwords. Enable name fields if email sign-ups should collect a display name. [Clerk sign-in and sign-up settings](https://clerk.com/docs/guides/configure/auth-strategies/sign-up-sign-in-options)
2. Under **SSO connections**, enable GitHub for all users. In production, supply custom GitHub OAuth credentials and register the callback URL Clerk provides. [Clerk GitHub setup](https://clerk.com/docs/guides/configure/auth-strategies/social-connections/github)
3. Under **Webhooks**, point the endpoint at `https://<fastapi-host>/webhooks/clerk` and subscribe to `user.created`, `user.updated`, and `user.deleted`. Set `CLERK_WEBHOOK_SIGNING_SECRET` in FastAPI from that endpoint. The endpoint needs a publicly reachable URL for Clerk to deliver events.
4. If sign-up requires express consent, configure the same public legal URLs in Clerk's **Legal** settings and enable consent there. [Clerk legal consent settings](https://clerk.com/docs/guides/secure/legal-compliance)

## FastAPI profile check

Set the matching `CLERK_SECRET_KEY` and `FRONTEND_URL` in FastAPI. The latter must exactly match the frontend origin, such as `http://localhost:3000`. Set `MONGODB_URI` to the connection string and optionally `MONGODB_DB_NAME` to the database name.

If a webhook attempt shows `POST /` and returns 405, add `/webhooks/clerk` to the endpoint URL in Clerk. Then replay the failed `user.created` delivery from **Message Attempts**. Signing in again with an existing account does not create another `user.created` event.

The dashboard uses `lib/api.js` to obtain a Clerk session token and send it in the Authorization header of `GET /api/users/me`. While the MongoDB profile is pending, it displays the available Clerk identity and retries a 404 after one, two, and four seconds. If synchronization still hasn't finished, the user can retry without signing out.

## GitHub App installation

Use the same GitHub App that sends workflow events to FastAPI. In its GitHub settings, set both the **Setup URL** and a **Callback URL** to `http://localhost:3000/dashboard/github` for local development. For deployment, use the frontend's HTTPS origin and set `FRONTEND_URL` to that origin in FastAPI. Leave **Request user authorization (OAuth) during installation** off so the Setup URL is available. Allow installation on **Any account** if the app should serve GitHub accounts and organizations beyond its owner. [GitHub App registration settings](https://docs.github.com/en/apps/maintaining-github-apps/modifying-a-github-app-registration)

Set these in the backend environment alongside its existing GitHub App ID, private key path, and webhook secret.

```dotenv
GITHUB_APP_SLUG=
GITHUB_APP_CLIENT_ID=
GITHUB_APP_CLIENT_SECRET=
```

Use the slug from `https://github.com/apps/<slug>` and the client ID and secret from this GitHub App. Keep the client secret in the backend. The Clerk GitHub sign-in connection has its own credentials.

On the dashboard, choose **Connect GitHub**. GitHub handles the choice of personal or organization account and repository access. After installation, choose **Verify installation** to link it to your Clerk account. If an organization owner must approve the request, wait for approval and then choose **Link existing installation**. That action also connects an app you installed earlier. FastAPI checks access through GitHub before saving the link. The dashboard rechecks whether linked installations are active or suspended.

```sh
npm run lint
npm run build
node --test lib/api.test.mjs
```

From `backend/`, run `uv run pytest tests/test_user_profile.py` for the endpoint integration test. It verifies that a signed session token resolves to the right profile and that a pending webhook sync returns a 404.
