import { notFound } from "next/navigation";
import {
  PartnerRunDetail,
  type PartnerRunSection,
} from "@/components/organisms/partner-run-detail";
export const metadata = { title: "Evaluations run" };
export default async function Page({
  params,
}: {
  params: Promise<{ id: string; section?: string[] }>;
}) {
  const { id, section } = await params;
  const tab = section?.[0] ?? "details";
  if ((section?.length ?? 0) > 1 || !["details", "related", "results", "logs"].includes(tab))
    notFound();
  return (
    <PartnerRunDetail key={id} kind="evaluations" id={id} section={tab as PartnerRunSection} />
  );
}
