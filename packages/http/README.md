The reader destination contract in [reader-destinations.json](src/reader-destinations.json)
defines route identities, semantic path parameters, approved query fields, navigation metadata,
and privacy-safe labels. The reader and browser extensions resolve navigation from its TypeScript
consumer; the user API validates login destinations with its Python consumer. The JSON is included
in the Python package and its build cache inputs.

Add or change destinations in that contract instead of maintaining an authentication allowlist.
Finite parameter values map semantic identities to URL segments, such as `tutorial` to `tutorials`.
Routes explicitly declare whether they can be login destinations and whether the embedded reader
owns them. The `/settings` alias resolves to the profile section. Unknown routes and query fields,
duplicate query keys, invalid parameter values, and unsafe URLs are rejected.

The public login API keeps its relative `return_to` URL parameter. It resolves that URL to
`{routeId, parameters, query}` before starting authentication and stores this record in the bound
OIDC flow. The callback validates the record again before exchanging the provider code or creating
a session, then serializes one canonical relative URL. Invalid UI links fall back to the reader
home; invalid direct API inputs return 422. The extension's sign-in flow uses its declared
completion destination and preserves the current reader page.

Run `npm run test:reader-destinations` to compare both runtimes against every declared destination
and malformed URL or stored-record cases. The scoped commit checks run this suite for contract or
consumer changes, alongside Python and frontend checks. Browser regressions for actual sign-in
links run with `DEVFEED_LOGIN_DESTINATIONS_ONLY=1 node apps/web/tests/browser/feed.mjs` and the
same flag on `apps/extensions/tests/reader.browser.mjs`, once the production web and extension
packages have been built. Set `DEVFEED_EXTENSION_BROWSER=edge` for the Edge package.
