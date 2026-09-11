import Link from "next/link";
import { UserAccount, PersonalFeedNav } from "./user-account";
import {
  ArrowUpRight,
  CornerDownLeft,
  Braces,
  Compass,
  Hash,
  House,
  Rss,
  Search,
} from "lucide-react";
import type { Topic } from "@/lib/types";
import { feedParams, type FeedFilters } from "@/lib/feed-query";

export function UserShell({
  children,
  topics = [],
  filters,
  section = "feed",
}: {
  children: React.ReactNode;
  topics?: Topic[];
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
        <Link href="/" className="brand" aria-label="DevFeed home">
          <Braces size={27} strokeWidth={2.5} />
          <span>
            devfeed<span className="brand-dot">.</span>
          </span>
        </Link>
        <form action="/" className="search" role="search">
          <Search size={20} aria-hidden="true" />
          <label className="sr-only" htmlFor="search">
            Search articles
          </label>
          <input
            id="search"
            type="search"
            name="q"
            placeholder="Search developer articles"
            defaultValue={filters?.q}
            maxLength={200}
          />
          {filters &&
            [...feedParams({ ...filters, q: "", cursor: "" })].map(
              ([name, value]) => (
                <input key={name} type="hidden" name={name} value={value} />
              ),
            )}
          <button type="submit" aria-label="Submit search">
            <CornerDownLeft size={16} />
          </button>
        </form>
        <UserAccount />
      </header>
      <aside className="sidebar" aria-label="Primary navigation">
        <nav>
          <PersonalFeedNav active={section === "personal"} />
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
        {!!topics.length && (
          <div className="sidebar-topics">
            <p className="eyebrow">Topics</p>
            {topics.slice(0, 10).map((topic) => (
              <Link
                key={topic.id}
                href={`/topics/${encodeURIComponent(topic.slug)}`}
                className={`nav-item topic-nav ${filters?.topic === topic.slug ? "active" : ""}`}
              >
                <Hash size={16} />
                <span>{topic.name}</span>
              </Link>
            ))}
            <Link className="browse-all" href="/topics">
              Browse all topics <ArrowUpRight size={14} />
            </Link>
          </div>
        )}
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
