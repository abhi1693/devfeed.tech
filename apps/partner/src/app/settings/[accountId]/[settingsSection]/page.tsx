import { notFound } from "next/navigation";
import { PortalPage, type PortalSearchParams } from "@/lib/server/portal-page";

export const dynamic = "force-dynamic";
export const metadata = { title: "Account settings" };

export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ accountId: string; settingsSection: string }>;
  searchParams: Promise<PortalSearchParams>;
}) {
  const { accountId, settingsSection } = await params;
  const section = (["destination", "formats", "audience"] as const).find(
    (value) => value === settingsSection,
  );
  if (!section) notFound();
  return (
    <PortalPage
      section="settings"
      settingsSection={section}
      accountId={accountId}
      searchParams={searchParams}
    />
  );
}
