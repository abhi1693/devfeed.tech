import { AdminLayout } from "@/components/templates/admin-layout";
import { Overview } from "@/components/organisms/overview";
import { initialOverview, requireAdmin } from "@/lib/server/session";

export const dynamic = "force-dynamic";

export default async function AdminPage() {
  const admin = await requireAdmin();
  const overview = await initialOverview();
  return <AdminLayout admin={admin}><Overview initialData={overview} /></AdminLayout>;
}
