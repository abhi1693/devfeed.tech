import { notFound } from "next/navigation";
import { PartnerProductPage } from "@/components/organisms/partner-product-page";
export const metadata = { title: "Product" };
export default async function Page({
  params,
}: {
  params: Promise<{ id: string; section?: string[] }>;
}) {
  const { id, section } = await params;
  const tab = section?.[0] ?? "details";
  if ((section?.length ?? 0) > 1 || !["details", "related"].includes(tab)) notFound();
  return <PartnerProductPage key={id} id={id} section={tab as "details" | "related"} />;
}
