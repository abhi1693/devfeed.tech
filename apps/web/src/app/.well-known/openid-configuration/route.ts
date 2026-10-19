import { privateApi, UserApiError } from "@/lib/server/api-client";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try {
    // No incoming cookies or authorization are forwarded by this public discovery read.
    const metadata = await privateApi.read<Record<string, unknown>>(
      "/.well-known/openid-configuration",
      { signal: request.signal },
    );
    const canonical = `${String(metadata.issuer).replace(/\/$/, "")}/.well-known/openid-configuration`;
    return Response.json(metadata, {
      headers: {
        "Cache-Control": "no-store",
        "Content-Location": canonical,
        Link: `<${canonical}>; rel="canonical"`,
        "Access-Control-Allow-Origin": "*",
      },
    });
  } catch (error) {
    const status = error instanceof UserApiError && error.status === 404 ? 404 : 503;
    return Response.json(
      {
        detail:
          status === 404
            ? "Reader authentication is not configured"
            : "Identity provider discovery is unavailable",
      },
      { status, headers: { "Cache-Control": "no-store" } },
    );
  }
}
