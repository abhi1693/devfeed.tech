# Backend API contracts

The public, admin and user FastAPI services expose their contracts at `/openapi.json`.
All owned JSON endpoints declare Pydantic response models. Request bodies use
Pydantic models; path, query, cookie and header inputs use typed FastAPI parameters
or dependencies. GET endpoints do not require artificial request bodies.

Contracts validate and filter JSON responses before serialization, including
health, version, ingestion status, topic relations and sitemap inventory. Sitemap
models preserve cache headers, optional publication dates and older cached shards
that contain only paths. Redis response-cache hits reuse previously serialized
responses; changing a cached payload's shape still requires a cache version change.

Shared HTTP models live in `packages/http/src/schemas.py`. They describe successful
health checks, readiness failures, version information and error responses. Errors
use `detail` containing a message, a structured code/message, or validation issues.
Validation issues include only `loc`, `msg` and `type`, never the rejected input.
OpenAPI documents common client/server errors and explicit route overrides.

Extensible metadata such as provenance and job details uses
`dict[str, JsonValue]`, with the recursive JSON type defined in
`packages/core/src/json_types.py`. It preserves provider-specific keys while
giving Python validation and generated clients an explicit JSON value union.

## Responses without JSON models

- Authentication redirects declare HTTP 302 and `RedirectResponse`. Callback
  query parameters have a shared model; repeated-parameter, state, issuer and code
  checks remain in the authentication flow so failures still clean up cookies
  and redirect to the login page.
- Successful deletes, logout and login cancellation declare HTTP 204 and
  `Response`, with no response body.
- The two authenticated notification inbox gateways forward Chimely's JSON and
  SSE protocol. Their explicit `Response` types remain outside the owned OpenAPI
  contract. The shared proxy enforces path, identity, query, body and stream bounds;
  it does not reinterpret the upstream SDK's request/response schemas.
- Next.js API routes forward these backend contracts; they do not define another
  set of domain request/response models.

## Changing an endpoint

Declare the request model before accepting JSON, and declare `response_model`
for every JSON success response. Reuse existing domain models where appropriate.
For manual `JSONResponse` error branches, validate the content with the documented
model before sending it. Preserve HTTP status codes, headers and nullability.

Run `uv run pytest tests/test_api_contracts.py` to check all service routes,
request models, OpenAPI schemas, serialization and error behavior. Existing auth,
CRUD, notification and sitemap suites cover the corresponding workflows. After
admin contract changes, run `npm run admin:generate` and check in the generated
OpenAPI and TypeScript client together.
