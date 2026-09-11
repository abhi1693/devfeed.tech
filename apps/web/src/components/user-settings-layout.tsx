import Link from "next/link";
import { Bell, Hash, UserRound } from "lucide-react";

export function UserSettingsLayout({ section, children }: { section: "profile" | "notifications"; children: React.ReactNode }) {
  return <div className="profile-settings">
    <header className="profile-settings-heading">
      <nav aria-label="Breadcrumb"><Link href="/" prefetch={false}>Home</Link></nav>
      <h1>Settings</h1>
    </header>
    <nav className="profile-settings-nav" aria-label="Settings sections">
      <Link href="/settings/profile" aria-current={section === "profile" ? "page" : undefined}><UserRound size={16} aria-hidden />Profile</Link>
      <Link href="/settings/notifications" aria-current={section === "notifications" ? "page" : undefined}><Bell size={16} aria-hidden />Notifications</Link>
      <Link href="/preferences"><Hash size={16} aria-hidden />Your topics</Link>
    </nav>
    {children}
  </div>;
}
