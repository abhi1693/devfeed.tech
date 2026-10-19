import { publicSiteOrigin } from "@/lib/server/config";

export const dynamic = "force-dynamic";

export function GET() {
  const origin = publicSiteOrigin();
  return Response.json(
    {
      linkset: [
        {
          anchor: `${origin}/api/v1`,
          "service-desc": [{ href: `${origin}/api/openapi.json`, type: "application/json" }],
          "service-doc": [{ href: `${origin}/api/docs`, type: "text/plain" }],
        },
      ],
    },
    {
      headers: {
        "Content-Type":
          'application/linkset+json; profile="https://www.rfc-editor.org/info/rfc9727"',
        "Cache-Control": "public, max-age=300",
        Link: `<${origin}/.well-known/api-catalog>; rel="api-catalog"`,
      },
    },
  );
}
