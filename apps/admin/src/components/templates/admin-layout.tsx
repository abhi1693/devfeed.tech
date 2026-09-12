import type { AdminIdentity, UserSettings } from "@/lib/api/generated/models";
import { AdminHeader } from "@/components/organisms/admin-header";
import { Sidebar } from "@/components/organisms/sidebar";
import { AdminSession } from "@/components/molecules/admin-session";

export function AdminLayout({
  admin,
  settings,
  children,
}: {
  admin: AdminIdentity;
  settings?: UserSettings;
  children: React.ReactNode;
}) {
  return (
    <AdminSession admin={admin} settings={settings}>
      <div className="min-h-dvh">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:bg-card focus:p-4"
        >
          Skip to content
        </a>
        <AdminHeader admin={admin} />
        <div className="lg:flex">
          <Sidebar />
          <main id="main" className="min-w-0 flex-1 px-4 py-6 sm:px-6">
            <div className="w-full min-w-0">{children}</div>
          </main>
        </div>
      </div>
    </AdminSession>
  );
}
