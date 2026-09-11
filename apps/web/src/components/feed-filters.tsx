"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { SlidersHorizontal, X } from "lucide-react";
import type { Source } from "@/lib/types";
import {
  contentTypes,
  feedHref,
  parseFilters,
  type FeedFilters,
} from "@/lib/feed-query";

const typeLabels: Record<string, string> = {
  "": "All",
  article: "Articles",
  news: "News",
  tutorial: "Tutorials",
  release: "Releases",
  comparison: "Comparisons",
  opinion: "Opinions",
};

const languages = [
  ["en", "English"],
  ["es", "Spanish"],
  ["fr", "French"],
  ["de", "German"],
  ["pt", "Portuguese"],
  ["ja", "Japanese"],
  ["zh", "Chinese"],
  ["ko", "Korean"],
  ["hi", "Hindi"],
];

export function FeedFiltersBar({
  filters,
  sources,
}: {
  filters: FeedFilters;
  sources: Source[];
}) {
  const router = useRouter();
  const formFilters = { ...filters, cursor: "", language: "", source_id: "" };
  const formUrl = new URL(feedHref(formFilters), "http://localhost");
  const active = Object.entries(filters).filter(
    ([key, value]) => value && key !== "cursor",
  );
  return (
    <>
      <div className="feed-toolbar">
        <nav className="feed-tabs" aria-label="Article type">
          {["", ...contentTypes].map((type) => (
            <Link
              key={type}
              href={feedHref(filters, { content_type: type })}
              className={filters.content_type === type ? "selected" : ""}
              aria-current={filters.content_type === type ? "page" : undefined}
            >
              {typeLabels[type]}
            </Link>
          ))}
        </nav>
        <details className="filter-menu">
          <summary>
            <SlidersHorizontal size={17} />
            Filters
            {(filters.language || filters.source_id) && (
              <span className="filter-count">
                {Number(!!filters.language) + Number(!!filters.source_id)}
              </span>
            )}
          </summary>
          <form action={formUrl.pathname} className="filter-popover" onSubmit={(event) => {
            event.preventDefault();
            const values = Object.fromEntries(new FormData(event.currentTarget)) as Record<string, string>;
            router.push(feedHref(parseFilters({ ...formFilters, ...values })));
          }}>
            {[
              ...formUrl.searchParams,
            ].map(([name, value]) => (
              <input type="hidden" key={name} name={name} value={value} />
            ))}
            <label htmlFor="language">Language</label>
            <select
              id="language"
              name="language"
              defaultValue={filters.language}
            >
              <option value="">All languages</option>
              {filters.language &&
                !languages.some(([code]) => code === filters.language) && (
                  <option value={filters.language}>{filters.language}</option>
                )}
              {languages.map(([code, name]) => (
                <option key={code} value={code}>
                  {name}
                </option>
              ))}
            </select>
            <label htmlFor="source">Source</label>
            <select
              id="source"
              name="source_id"
              defaultValue={filters.source_id}
            >
              <option value="">All sources</option>
              {filters.source_id &&
                !sources.some((source) => source.id === filters.source_id) && (
                  <option value={filters.source_id}>Selected source</option>
                )}
              {sources.map((source) => (
                <option key={source.id} value={source.id}>
                  {source.name}
                </option>
              ))}
            </select>
            <button className="button primary" type="submit">
              Apply filters
            </button>
          </form>
        </details>
      </div>
      {!!active.length && (
        <div className="active-filters">
          {active.map(([key, value]) => (
            <Link
              key={key}
              href={feedHref(filters, { [key]: "" })}
              className="filter-chip"
              aria-label={`Remove ${key} filter`}
            >
              {key === "source_id"
                ? (sources.find((s) => s.id === value)?.name ?? "Source")
                : value}
              <X size={13} />
            </Link>
          ))}
          <Link href="/" className="clear-filters">
            Clear all
          </Link>
        </div>
      )}
    </>
  );
}
