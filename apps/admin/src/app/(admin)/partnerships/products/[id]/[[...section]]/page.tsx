import { notFound } from "next/navigation";
import { PartnerProductPage } from "@/components/organisms/partner-product-page";
import { resolvePartnerDetailSection } from "@/lib/partner-detail-routes";
export const metadata = { title: "Product" };
export default async function Page({
  params,
}: {
  params: Promise<{ id: string; section?: string[] }>;
}) {
  const { id, section } = await params;
  const tab = resolvePartnerDetailSection("products", section);
  if (!tab) notFound();
  return <PartnerProductPage key={id} id={id} section={tab} />;
}
