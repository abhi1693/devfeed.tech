import { privateApi, UserApiError } from "@/lib/server/api-client";

export const dynamic = "force-dynamic";

type ResourceMetadata = {
  resource: string;
  authorization_servers: string[];
  bearer_methods_supported: string[];
};

export async function GET(request: Request) {
  try {
    // Read public configuration only; never forward incoming reader credentials.
    const metadata = await privateApi.read<ResourceMetadata>(
      "/.well-known/oauth-protected-resource",
      { signal: request.signal },
    );
    const canonical = new URL(metadata.resource);
    const resourcePath = canonical.pathname === "/" ? "" : canonical.pathname;
    canonical.pathname = `/.well-known/oauth-protected-resource${resourcePath}`;
    return Response.json(metadata, {
      headers: {
        "Cache-Control": "no-store",
        "Access-Control-Allow-Origin": "*",
        "Content-Location": canonical.href,
        Link: `<${canonical.href}>; rel="canonical"`,
      },
    });
  } catch (error) {
    const status = error instanceof UserApiError && error.status === 404 ? 404 : 503;
    return Response.json(
      {
        detail:
          status === 404
            ? "Agent authentication is not configured"
            : "Agent discovery is unavailable",
      },
      { status, headers: { "Cache-Control": "no-store" } },
    );
  }
}
