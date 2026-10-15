import { notFound } from "next/navigation";
import { PartnerRunDetail } from "@/components/organisms/partner-run-detail";
import { resolvePartnerDetailSection } from "@/lib/partner-detail-routes";
export const metadata = { title: "Evaluations run" };
export default async function Page({
  params,
}: {
  params: Promise<{ id: string; section?: string[] }>;
}) {
  const { id, section } = await params;
  const tab = resolvePartnerDetailSection("evaluations", section);
  if (!tab) notFound();
  return <PartnerRunDetail key={id} kind="evaluations" id={id} section={tab} />;
}
