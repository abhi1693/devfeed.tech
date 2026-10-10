import { PortalPage, type PortalSearchParams } from "@/lib/server/portal-page";
export const dynamic = "force-dynamic";
export const metadata = { title: "Performance" };
export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ accountId: string }>;
  searchParams: Promise<PortalSearchParams>;
}) {
  const { accountId } = await params;
  return <PortalPage section="performance" accountId={accountId} searchParams={searchParams} />;
}
