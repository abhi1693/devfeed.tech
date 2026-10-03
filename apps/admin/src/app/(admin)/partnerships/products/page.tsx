import { redirect } from "next/navigation";
import { partnerProductHref } from "@/lib/partner-runs";
import { PartnerTools } from "@/components/organisms/partner-tools";
export const metadata = { title: "Products" };
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ provider?: string; product_id?: string }>;
}) {
  const { provider, product_id } = await searchParams;
  if (product_id) redirect(partnerProductHref(product_id));
  return <PartnerTools key={provider ?? ""} provider={provider} />;
}
