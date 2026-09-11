# Public user

Anonymous Next.js website on port 3000. Includes the newest-first article feed,
URL-based search and filters, topic and source directories, and article previews
with original-publisher links. Public browsing uses the anonymous API. Optional sign-in, followed topics, and
My feed use the separate user API through a same-origin gateway.

From the repository root, run `npm ci` then `npm run web:dev`. Set
`DEVFEED_PUBLIC_API_URL` to the API origin (default `http://127.0.0.1:8000`).
For user sign-in, also set `DEVFEED_USER_API_URL` and
`DEVFEED_USER_BASE_URL`. See [user accounts](../../docs/user-accounts.md).
Run `npm run web:lint`, `npm run web:test`, and `npm run web:build` to validate.

See [development](../../docs/development.md) and [Compose](../../docs/compose.md)
for deployment and runtime configuration.
