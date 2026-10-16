import Link from "@/components/reader-link";
import { Bell, Hash, LayoutGrid, Palette, Rss, UserRound } from "lucide-react";
import {
  readerSettingsRoutes,
  type ReaderSettingsSection,
} from "@devfeed/http/reader-destinations";

export function UserSettingsLayout({
  section,
  children,
}: {
  section: ReaderSettingsSection;
  children: React.ReactNode;
}) {
  return (
    <div className="profile-settings">
      <header className="profile-settings-heading">
        <nav aria-label="Breadcrumb">
          <Link href="/" prefetch={false}>
            Home
          </Link>
        </nav>
        <h1>Settings</h1>
      </header>
      <nav className="profile-settings-nav" aria-label="Settings sections">
        <Link
          href={`/settings/${readerSettingsRoutes.profile}`}
          aria-current={section === "profile" ? "page" : undefined}
        >
          <UserRound size={16} aria-hidden />
          Your Dev Card
        </Link>
        <Link
          href={`/settings/${readerSettingsRoutes.appearance}`}
          aria-current={section === "appearance" ? "page" : undefined}
        >
          <Palette size={16} aria-hidden />
          Appearance
        </Link>
        <Link
          href={`/settings/${readerSettingsRoutes.notifications}`}
          aria-current={section === "notifications" ? "page" : undefined}
        >
          <Bell size={16} aria-hidden />
          Notifications
        </Link>
        <Link
          href={`/settings/${readerSettingsRoutes.feed}`}
          aria-current={section === "feed" ? "page" : undefined}
        >
          <LayoutGrid size={16} aria-hidden />
          Feed
        </Link>
        <Link
          href={`/settings/${readerSettingsRoutes.topics}`}
          aria-current={section === "topics" ? "page" : undefined}
        >
          <Hash size={16} aria-hidden />
          Your topics
        </Link>
        <Link
          href={`/settings/${readerSettingsRoutes.sources}`}
          aria-current={section === "sources" ? "page" : undefined}
        >
          <Rss size={16} aria-hidden />
          Your sources
        </Link>
      </nav>
      {children}
    </div>
  );
}
