import { AdminLayout } from "@/components/templates/admin-layout";
import { requireAdmin, initialUserSettings } from "@/lib/server/session";
export const dynamic = "force-dynamic";
export default async function Layout({ children }: { children: React.ReactNode }) {
  const admin = await requireAdmin();
  return (
    <AdminLayout admin={admin} settings={await initialUserSettings()}>
      {children}
    </AdminLayout>
  );
}
