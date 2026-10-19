import { mcpPublicUrl, publicSiteOrigin } from "@/lib/server/config";

export const dynamic = "force-dynamic";

export function GET() {
  const origin = publicSiteOrigin();
  let resource: string | null;
  try {
    resource = mcpPublicUrl();
  } catch {
    return new Response("Agent authentication configuration is unavailable", {
      status: 503,
      headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
    });
  }
  const discovery = resource
    ? `The configured MCP resource is ${resource}.
Fetch its Protected Resource Metadata at ${
        new URL(
          `/.well-known/oauth-protected-resource${new URL(resource).pathname.replace(/\/$/, "")}`,
          resource,
        ).href
      }.
If the server returns a 401 challenge, prefer the resource_metadata URL in WWW-Authenticate.
Read resource, authorization_servers, scopes_supported, and bearer_methods_supported.
For the advertised authorization server, fetch its /.well-known/oauth-authorization-server metadata.
Use its exact issuer, registration_endpoint, authorization_endpoint, token_endpoint, and revocation_endpoint; do not infer these from the reader website origin.
If metadata is unavailable, stop account authorization and use only anonymous public tools.`
    : `No MCP resource URL is configured for this reader deployment.
See ${origin}/mcp for setup. Do not guess a registration, token, or authorization endpoint.`;

  return new Response(
    `# DevFeed auth.md

For agents and OAuth-capable MCP clients connecting to DevFeed on behalf of a reader.
Public article discovery requires no account. Account tools use OAuth public-client registration,
browser sign-in and consent, and authorization code exchange with S256 PKCE.

## 1. Discover

${discovery}

## 2. Register an OAuth client

When the resource publishes OAuth metadata, POST JSON to its registration_endpoint:

\`\`\`json
{
  "client_name": "Your MCP client",
  "redirect_uris": ["https://your-client.example/oauth/callback"],
  "token_endpoint_auth_method": "none",
  "grant_types": ["authorization_code", "refresh_token"],
  "response_types": ["code"]
}
\`\`\`

Replace the example redirect with your client's actual callback before registering.
HTTPS callbacks are supported; HTTP is permitted only for loopback callbacks.
Keep the returned client_id. Client registration does not create a reader account,
grant access to account data, or issue an access token. Do not register during a passive scan.

## 3. Obtain user authorization

Generate a fresh state and PKCE code_verifier, derive the S256 code_challenge, and open
the advertised authorization_endpoint in the user's browser with response_type=code,
client_id, redirect_uri, state, code_challenge, code_challenge_method=S256, resource,
and the requested scope. The user signs in and approves or rejects access on DevFeed.
Verify returned state and issuer before exchanging the code at token_endpoint using
application/x-www-form-urlencoded with grant_type=authorization_code, client_id,
code, redirect_uri, code_verifier, and the MCP resource value.

## 4. Scopes and credentials

- devfeed:read: read the user's feed, bookmarks, Must Reads, and followed topics and sources.
- devfeed:write: explicitly approved bookmark, follow, and like changes.
- offline_access: request renewable access when advertised by the authorization server.

Use Authorization: Bearer <access_token> only at the MCP resource. A bearer token for
MCP is not a reader-session cookie or authorization for the website's account gateway.
Store credentials securely; never put tokens, cookies, or CSRF values in prompts or URLs.
Refresh using grant_type=refresh_token at token_endpoint. Serialize refreshes and save
the replacement refresh token; replaying a rotated token revokes the connection.
If authorization expires or is revoked, ask the user to reconnect. Never retry writes blindly.

## 5. Revocation and supported registration methods

Use the advertised revocation_endpoint to revoke credentials, or let the user disconnect
an agent from Connected agents on ${origin}/mcp.
The supported method is OAuth dynamic client registration followed by interactive
user consent and PKCE. Auth.md identity-assertion, verified-email, anonymous account
registration, device-code claims, and JWT-bearer assertion exchange are not implemented.
Do not send those registration bodies or invent /agent/auth or /agent/identity endpoints.
The authorization server does not advertise an agent_auth block for those unsupported flows.

## Policies and help

Setup: ${origin}/mcp
Terms: ${origin}/legal/terms
Privacy: ${origin}/legal/privacy
Integration issues: https://github.com/abhi1693/devfeed.tech/issues
`,
    {
      headers: {
        "Content-Type": "text/markdown; charset=utf-8",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
      },
    },
  );
}
