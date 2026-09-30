import { McpConsent } from "@/components/mcp-consent";
import { UserShell } from "@/components/user-shell";

export const dynamic = "force-dynamic";
export const metadata = {
  title: "Authorize agent | DevFeed",
  robots: { index: false, follow: false },
};

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ request?: string }>;
}) {
  const params = await searchParams;
  return (
    <UserShell section="mcp">
      <McpConsent requestId={params.request ?? ""} />
    </UserShell>
  );
}
