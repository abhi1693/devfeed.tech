import { PortalPage, type PortalSearchParams } from "@/lib/server/portal-page";
export const dynamic = "force-dynamic";
export const metadata = { title: "Product placements" };
export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ productId: string }>;
  searchParams: Promise<PortalSearchParams>;
}) {
  const { productId } = await params;
  return (
    <PortalPage
      section="products"
      searchParams={searchParams}
      productRoute={{ view: "placements", productId }}
    />
  );
}
