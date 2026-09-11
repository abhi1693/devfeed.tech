import Link from "next/link";
import Image from "next/image";
import brandMark from "@devfeed/theme/assets/devfeed-mark.png";
import { NotificationInbox } from "./notification-inbox";
import { ThemeToggle } from "./theme-toggle";
import { UserAccount, PersonalFeedNav } from "./user-account";
import { Compass, House, Rss } from "lucide-react";
import { UserSearch } from "./user-search";
import type { FeedFilters } from "@/lib/feed-query";

export function UserShell({
  children,
  filters,
  section = "feed",
}: {
  children: React.ReactNode;
  filters?: FeedFilters;
  section?:
    | "feed"
    | "topics"
    | "article"
    | "sources"
    | "account"
    | "personal"
    | "trending";
}) {
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <Link href="/" className="brand devfeed-brand" aria-label="DevFeed home">
          <Image
            className="devfeed-brand-mark"
            src={brandMark}
            alt=""
            width={40}
            height={40}
            priority
          />
          <span>devfeed.</span>
        </Link>
        <UserSearch filters={filters} />
        <div className="header-actions">
          <ThemeToggle />
          <NotificationInbox />
          <UserAccount />
        </div>
      </header>
      <aside className="sidebar" aria-label="Primary navigation">
        <nav>
          <Link
            href="/"
            className={`nav-item ${section === "feed" && !filters?.topic ? "active" : ""}`}
            aria-current={
              section === "feed" && !filters?.topic ? "page" : undefined
            }
          >
            <House size={20} />
            <span>Latest feed</span>
          </Link>
          <PersonalFeedNav active={section === "personal"} />
          <Link
            href="/topics"
            className={`nav-item ${section === "topics" ? "active" : ""}`}
            aria-current={section === "topics" ? "page" : undefined}
          >
            <Compass size={20} />
            <span>Explore topics</span>
          </Link>
          <Link
            href="/sources"
            className={`nav-item ${section === "sources" ? "active" : ""}`}
            aria-current={section === "sources" ? "page" : undefined}
          >
            <Rss size={20} />
            <span>Sources</span>
          </Link>
        </nav>
      </aside>
      <main id="main" className="main-content">
        {children}
      </main>
      <footer className="mobile-nav">
        <PersonalFeedNav mobile active={section === "personal"} />
        <Link href="/">
          <House size={20} />
          Latest
        </Link>
        <Link href="/topics">
          <Compass size={20} />
          Topics
        </Link>
        <Link href="/sources">
          <Rss size={20} />
          Sources
        </Link>
      </footer>
    </>
  );
}
