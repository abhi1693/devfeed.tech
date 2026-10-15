import { notFound, redirect } from "next/navigation";
import { requireAdmin } from "@/lib/server/session";
import type { AccountSettingsTab } from "@/components/organisms/partner-account-management";
import { PartnerAccountPage } from "@/components/organisms/partner-account-page";
import { partnerAccountHref, resolvePartnerAccountRoute } from "@/lib/partner-account-routes";
export const metadata = { title: "Partner account" };
export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ id: string; section?: string[] }>;
  searchParams: Promise<{ offset?: string; limit?: string; days?: string; tab?: string }>;
}) {
  const admin = await requireAdmin();
  if (!admin.roles.includes("superuser")) redirect("/");
  const { id, section: segments } = await params;
  const route = resolvePartnerAccountRoute(id, segments);
  if (!route) notFound();
  const query = await searchParams;
  const offset = Number(query.offset ?? 0);
  const limit = Number(query.limit ?? 100);
  if (!Number.isSafeInteger(offset) || offset < 0) notFound();
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 200) notFound();
  const days = Number(query.days ?? 30);
  if (![7, 30, 90].includes(days)) notFound();
  const settingsTabs = ["general", "delivery", "formats", "audience"] as const;
  const settingsTab = query.tab ?? "general";
  if (!settingsTabs.includes(settingsTab as AccountSettingsTab)) notFound();
  return (
    <PartnerAccountPage
      key={partnerAccountHref(route)}
      route={route}
      initialOffset={offset}
      initialLimit={limit}
      initialDays={days}
      initialSettingsTab={settingsTab as AccountSettingsTab}
    />
  );
}
