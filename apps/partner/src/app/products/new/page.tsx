import { PortalPage, type PortalSearchParams } from "@/lib/server/portal-page";
export const dynamic = "force-dynamic";
export const metadata = { title: "Add product" };
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<PortalSearchParams>;
}) {
  return (
    <PortalPage section="products" searchParams={searchParams} productRoute={{ view: "create" }} />
  );
}
