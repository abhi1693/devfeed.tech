import { notFound, redirect } from "next/navigation";
import { requireAdmin } from "@/lib/server/session";
import {
  PartnerAccountPage,
  type AccountSection,
} from "@/components/organisms/partner-account-page";
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
  const section = segments?.join("/") ?? "details";
  if (
    !["details", "edit", "related", "members/new", "assets/new"].includes(section) &&
    !/^assets\/[^/]+\/edit$/.test(section)
  )
    notFound();
  const query = await searchParams;
  const offset = Number(query.offset ?? 0);
  const limit = Number(query.limit ?? 100);
  if (!Number.isSafeInteger(offset) || offset < 0) notFound();
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 200) notFound();
  return (
    <PartnerAccountPage
      key={`${id}/${section}`}
      id={id}
      initialOffset={offset}
      initialLimit={limit}
      section={section as AccountSection}
    />
  );
}
