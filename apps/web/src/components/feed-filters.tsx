"use client";

import Link from "next/link";
import { useState } from "react";
import { Select } from "@devfeed/ui/select";
import { useRouter } from "next/navigation";
import { SlidersHorizontal, X } from "lucide-react";
import type { Source } from "@/lib/types";
import { useFeedPreferences } from "./feed-preferences";
import { contentTypes, feedHref, parseFilters, type FeedFilters } from "@/lib/feed-query";

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
  availableTypes = contentTypes,
  availableLanguages = languages.map(([code]) => code),
  topicPage = false,
}: {
  filters: FeedFilters;
  sources: Source[];
  availableTypes?: readonly string[];
  availableLanguages?: string[];
  topicPage?: boolean;
}) {
  const router = useRouter();
  const { content_types, loading, unavailable } = useFeedPreferences();
  const visibleTypes =
    loading || unavailable
      ? []
      : contentTypes.filter(
          (type) => availableTypes.includes(type) && content_types.includes(type),
        );
  const [language, setLanguage] = useState(filters.language);
  const [sourceId, setSourceId] = useState(filters.source_id);
  const formFilters = { ...filters, cursor: "", language: "", source_id: "" };
  const formUrl = new URL(feedHref(formFilters), "http://localhost");
  const active = Object.entries(filters).filter(
    ([key, value]) => value && key !== "cursor" && !(topicPage && key === "topic"),
  );
  return (
    <>
      <div className="feed-toolbar">
        <nav className="feed-tabs" aria-label="Article type">
          {["", ...visibleTypes].map((type) => (
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
          <form
            action={formUrl.pathname}
            className="filter-popover"
            onSubmit={(event) => {
              event.preventDefault();
              const values = Object.fromEntries(new FormData(event.currentTarget)) as Record<
                string,
                string
              >;
              router.push(feedHref(parseFilters({ ...formFilters, ...values })));
            }}
          >
            {[...formUrl.searchParams].map(([name, value]) => (
              <input type="hidden" key={name} name={name} value={value} />
            ))}
            <label htmlFor="language">Language</label>
            <Select
              id="language"
              name="language"
              label="Language"
              value={language}
              onChange={setLanguage}
              clearLabel="All languages"
              placeholder="All languages"
              options={availableLanguages.map((value) => ({
                value,
                label: languages.find(([code]) => code === value)?.[1] ?? value,
              }))}
            />
            <label htmlFor="source">Source</label>
            <Select
              id="source"
              name="source_id"
              label="Source"
              value={sourceId}
              onChange={setSourceId}
              clearLabel="All sources"
              placeholder="All sources"
              search={{}}
              options={sources.map((source) => ({ value: source.id, label: source.name }))}
            />
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
          <Link
            href={topicPage ? feedHref(parseFilters({ topic: filters.topic })) : "/"}
            className="clear-filters"
          >
            Clear all
          </Link>
        </div>
      )}
    </>
  );
}
