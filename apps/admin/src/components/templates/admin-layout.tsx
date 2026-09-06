import type { AdminIdentity } from "@/lib/api/generated/models";
import { AdminHeader } from "@/components/organisms/admin-header";
import { AdminSession } from "@/components/molecules/admin-session";

export function AdminLayout({ admin, children }: { admin: AdminIdentity; children: React.ReactNode }) {
  return <AdminSession admin={admin}><div className="min-h-dvh">
    <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:bg-card focus:p-4">Skip to content</a>
    <AdminHeader admin={admin} />
    <div className="lg:flex"><main id="main" className="min-w-0 flex-1 px-5 py-8 sm:px-8"><div className="mx-auto max-w-7xl">{children}</div></main></div>
  </div></AdminSession>;
}
