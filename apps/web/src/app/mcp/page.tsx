import { UserShell } from "@/components/user-shell";
import { McpContent } from "@/components/mcp-content";
import { canonicalUrl, pageMetadata } from "@/lib/metadata";
import { mcpPublicUrl } from "@/lib/server/config";
import { JsonLd } from "@/components/json-ld";
import { pageStructuredData } from "@/lib/structured-data";
import { mcpPageDescription, mcpPageTitle } from "@/lib/mcp";

export const dynamic = "force-dynamic";

export const metadata = {
  ...pageMetadata(mcpPageTitle, mcpPageDescription, canonicalUrl("/mcp")),
  title: { absolute: mcpPageTitle },
};

export default function Page() {
  return (
    <UserShell section="mcp">
      <JsonLd
        data={pageStructuredData(canonicalUrl("/mcp"), mcpPageTitle, {
          description: mcpPageDescription,
        })}
      />
      <McpContent initialEndpoint={mcpPublicUrl()} />
    </UserShell>
  );
}
