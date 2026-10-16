import { PortalPage, type PortalSearchParams } from "@/lib/server/portal-page";
export const dynamic = "force-dynamic";
export const metadata = { title: "Create import connection" };
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<PortalSearchParams>;
}) {
  return (
    <PortalPage
      section="connections"
      searchParams={searchParams}
      connectionsRoute={{ view: "create" }}
    />
  );
}
