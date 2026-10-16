import { redirect } from "next/navigation";
import { requireAdmin } from "@/lib/server/session";
import { PartnerAccounts } from "@/components/organisms/partner-accounts";
export const metadata = { title: "Partner accounts" };
export default async function Page() {
  const admin = await requireAdmin();
  if (!admin.roles.includes("superuser")) redirect("/");
  return <PartnerAccounts />;
}
