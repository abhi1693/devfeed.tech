import { notFound, redirect } from "next/navigation";
import { requireAdmin } from "@/lib/server/session";
import { PartnerAccountPage } from "@/components/organisms/partner-account-page";
import { partnerAccountHref, resolvePartnerAccountRoute } from "@/lib/partner-account-routes";
export const metadata = { title: "Partner account" };
export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ id: string; section?: string[] }>;
  searchParams: Promise<{ offset?: string; limit?: string }>;
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
  return (
    <PartnerAccountPage
      key={partnerAccountHref(route)}
      route={route}
      initialOffset={offset}
      initialLimit={limit}
    />
  );
}
