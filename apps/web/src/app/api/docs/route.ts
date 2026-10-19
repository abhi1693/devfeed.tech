import { publicSiteOrigin } from "@/lib/server/config";

export const dynamic = "force-dynamic";

export function GET() {
  const origin = publicSiteOrigin();
  return new Response(
    `# DevFeed Reader API

API catalog: ${origin}/.well-known/api-catalog
OpenAPI description: ${origin}/api/openapi.json

Public discovery
GET ${origin}/api/v1/feed returns published article previews.
GET ${origin}/api/v1/topics, /api/v1/sources, and /api/v1/tags return public directories.
GET ${origin}/api/v1/search?q=python searches public content.
Article previews include original-publisher links; DevFeed does not host the publishers' full articles.
Follow next_cursor when supplied. Pass it as cursor on the next feed request.

Optional accounts
The /api/v1/user routes support reader accounts through the existing OIDC sign-in flow.
Protected operations require the reader session cookie. These are not API-key endpoints.
For authenticated writes, send the csrf_token returned by /api/v1/user/auth/me in the X-CSRF-Token header and the configured reader Origin. Cookie and origin checks still apply.
Never share session cookies or CSRF tokens. Account endpoints are not anonymous discovery feeds.
Consult the OpenAPI operation security requirements and response schemas before making requests.

Errors and availability
Check HTTP status codes. Authentication, validation, rate limiting, and upstream availability can reject requests.
Respect Retry-After when present. Do not retry writes blindly.
The catalog does not expose admin APIs, internal service addresses, or operational health endpoints.
`,
    {
      headers: {
        "Content-Type": "text/plain; charset=utf-8",
        "Cache-Control": "public, max-age=300",
      },
    },
  );
}
