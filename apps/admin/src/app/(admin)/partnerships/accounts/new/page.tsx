import { redirect } from "next/navigation";
import { requireAdmin } from "@/lib/server/session";
import { PartnerAccountPage } from "@/components/organisms/partner-account-page";
export const metadata = { title: "Create partner account" };
export default async function Page() {
  const admin = await requireAdmin();
  if (!admin.roles.includes("superuser")) redirect("/");
  return <PartnerAccountPage />;
}
