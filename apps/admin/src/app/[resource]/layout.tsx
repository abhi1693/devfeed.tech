import { notFound } from "next/navigation";
import { isResource } from "@/lib/resources";
import { requireAdmin } from "@/lib/server/session";
import { AdminLayout } from "@/components/templates/admin-layout";
export const dynamic = "force-dynamic";
export default async function ResourceLayout({ children, params }: { children: React.ReactNode; params: Promise<{ resource: string }> }) {
  const { resource } = await params;
  if (!isResource(resource)) notFound();
  const admin = await requireAdmin();
  return <AdminLayout admin={admin}>{children}</AdminLayout>;
}
