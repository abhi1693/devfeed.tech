import { PortalPage, type PortalSearchParams } from "@/lib/server/portal-page";
export const dynamic = "force-dynamic";
export const metadata = { title: "Import connection" };
export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ connectionId: string }>;
  searchParams: Promise<PortalSearchParams>;
}) {
  const { connectionId } = await params;
  return (
    <PortalPage
      section="connections"
      searchParams={searchParams}
      connectionsRoute={{ view: "detail", connectionId }}
    />
  );
}
