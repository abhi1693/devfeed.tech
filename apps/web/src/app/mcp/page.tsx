import { UserShell } from "@/components/user-shell";
import { McpContent } from "@/components/mcp-content";
import { canonicalUrl, pageMetadata } from "@/lib/metadata";
import { mcpPublicUrl } from "@/lib/server/config";

export const dynamic = "force-dynamic";

export const metadata = pageMetadata(
  "Connect your agent",
  "Connect your AI agent to DevFeed with MCP. Explore developer articles, topics, and publications with six read-only tools.",
  canonicalUrl("/mcp"),
);

export default function Page() {
  return (
    <UserShell section="mcp">
      <McpContent initialEndpoint={mcpPublicUrl()} />
    </UserShell>
  );
}
