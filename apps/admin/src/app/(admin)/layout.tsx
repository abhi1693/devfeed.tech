import { AdminLayout } from "@/components/templates/admin-layout";
import { requireAdmin } from "@/lib/server/session";
export const dynamic = "force-dynamic";
export default async function Layout({ children }: { children: React.ReactNode }) {
  return <AdminLayout admin={await requireAdmin()}>{children}</AdminLayout>;
}
