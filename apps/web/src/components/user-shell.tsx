import Link from "next/link";
import Image from "next/image";
import brandMark from "@devfeed/theme/assets/devfeed-mark.png";
import { NotificationInbox } from "./notification-inbox";
import { ThemeToggle } from "./theme-toggle";
import { UserAccount, PersonalFeedNav } from "./user-account";
import { Compass, Heart, House, Rss } from "lucide-react";
import { UserSearch } from "./user-search";
import type { FeedFilters } from "@/lib/feed-query";

export function UserShell({
  children,
  filters,
  searchQuery,
  section = "feed",
}: {
  children: React.ReactNode;
  filters?: FeedFilters;
  searchQuery?: string;
  section?:
    "feed" | "topics" | "article" | "sources" | "account" | "personal" | "trending" | "search";
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
        <UserSearch filters={filters} query={searchQuery} />
        <div className="header-actions">
          <a
            className="navbar-icon"
            href="https://github.com/abhi1693/devfeed.tech"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="DevFeed on GitHub (opens in a new tab)"
            title="DevFeed on GitHub"
          >
            <svg width="19" height="19" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
              <path d="M6.766 11.328c-2.063-.25-3.516-1.734-3.516-3.656 0-.781.281-1.625.75-2.188-.203-.515-.172-1.609.063-2.062.625-.078 1.468.25 1.968.703.594-.187 1.219-.281 1.985-.281.765 0 1.39.094 1.953.265.484-.437 1.344-.765 1.969-.687.218.422.25 1.515.046 2.047.5.593.766 1.39.766 2.203 0 1.922-1.453 3.375-3.547 3.64.531.344.89 1.094.89 1.954v1.625c0 .468.391.734.86.547C13.781 14.359 16 11.53 16 8.03 16 3.61 12.406 0 7.984 0 3.563 0 0 3.61 0 8.031a7.88 7.88 0 0 0 5.172 7.422c.422.156.828-.125.828-.547v-1.25c-.219.094-.5.156-.75.156-1.031 0-1.64-.562-2.078-1.609-.172-.422-.36-.672-.719-.719-.187-.015-.25-.093-.25-.187 0-.188.313-.328.625-.328.453 0 .844.281 1.25.86.313.452.64.655 1.031.655s.641-.14 1-.5c.266-.265.47-.5.657-.656" />
            </svg>
          </a>
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
            aria-current={section === "feed" && !filters?.topic ? "page" : undefined}
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
        <div className="sidebar-support">
          <a
            href="https://www.patreon.com/asaharan"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="Fund the next commit on Patreon (opens in a new tab)"
          >
            <Heart size={17} aria-hidden="true" />
            <span>Fund the next commit</span>
          </a>
        </div>
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
