import { notFound } from "next/navigation";
import {
  PartnerRunDetail,
  type PartnerRunSection,
} from "@/components/organisms/partner-run-detail";
export const metadata = { title: "Pipeline jobs run" };
export default async function Page({
  params,
}: {
  params: Promise<{ id: string; section?: string[] }>;
}) {
  const { id, section } = await params;
  const tab = section?.[0] ?? "details";
  if ((section?.length ?? 0) > 1 || !["details", "related", "logs"].includes(tab)) notFound();
  return <PartnerRunDetail key={id} kind="pipeline" id={id} section={tab as PartnerRunSection} />;
}
