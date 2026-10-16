import { UserShell } from "@/components/user-shell";
import { McpContent } from "@/components/mcp-content";
import { canonicalUrl, pageMetadata } from "@/lib/metadata";
import { mcpPublicUrl } from "@/lib/server/config";
import { JsonLd } from "@/components/json-ld";
import { pageStructuredData } from "@/lib/structured-data";

export const dynamic = "force-dynamic";

const title = "Connect your agent";
const description =
  "Connect your AI agent to DevFeed with MCP. Explore developer articles, topics, and publications with six read-only tools.";
export const metadata = pageMetadata(title, description, canonicalUrl("/mcp"));

export default function Page() {
  return (
    <UserShell section="mcp">
      <JsonLd data={pageStructuredData(canonicalUrl("/mcp"), title, { description })} />
      <McpContent initialEndpoint={mcpPublicUrl()} />
    </UserShell>
  );
}
