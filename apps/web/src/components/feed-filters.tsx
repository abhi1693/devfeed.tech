"use client";
import { ExtensionInstallButton } from "./extension-install-button";
import { ReaderDisclosure } from "./reader-disclosure";
import { ReaderTabs } from "./reader-tabs";

import Link from "next/link";
import { useState } from "react";
import { Select } from "@devfeed/ui/select";
import { useRouter } from "next/navigation";
import { SlidersHorizontal, X } from "lucide-react";
import type { Source } from "@/lib/types";
import { useFeedPreferences } from "./feed-preferences";
import {
  contentTypes,
  feedHref,
  personalFeedHref,
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

export function FeedFiltersBar({
  filters,
  sources,
  availableTypes = contentTypes,
  topicPage = false,
  sourcePage = false,
  personal = false,
}: {
  filters: FeedFilters;
  sources: Source[];
  availableTypes?: readonly string[];
  topicPage?: boolean;
  sourcePage?: boolean;
  personal?: boolean;
}) {
  const router = useRouter();
  const hrefFor = personal ? personalFeedHref : feedHref;
  const { content_types, loading, unavailable } = useFeedPreferences();
  const visibleTypes =
    loading || unavailable
      ? []
      : contentTypes.filter(
          (type) => availableTypes.includes(type) && content_types.includes(type),
        );
  const [sourceId, setSourceId] = useState(filters.source_id);
  const formFilters = {
    ...filters,
    cursor: "",
    source_id: sourcePage ? filters.source_id : "",
    source_slug: sourcePage ? filters.source_slug : undefined,
  };
  const formUrl = new URL(hrefFor(formFilters), "http://localhost");
  const active = Object.entries(filters).filter(
    ([key, value]) =>
      value &&
      key !== "cursor" &&
      key !== "language" &&
      key !== "sort" &&
      key !== "source_slug" &&
      !(topicPage && key === "topic") &&
      !(sourcePage && key === "source_id"),
  );
  const filterCount = Number(!sourcePage && !!filters.source_id);
  return (
    <>
      <div className="feed-toolbar">
        <ReaderTabs
          scope={JSON.stringify({ ...filters, content_type: undefined })}
          selected={filters.content_type ?? ""}
        >
          {["", ...visibleTypes].map((type) => (
            <Link
              key={type}
              href={hrefFor(filters, { content_type: type })}
              className={filters.content_type === type ? "selected" : ""}
              aria-current={filters.content_type === type ? "page" : undefined}
            >
              {typeLabels[type]}
            </Link>
          ))}
        </ReaderTabs>
        <div className="feed-toolbar-actions">
          <label className="sr-only" htmlFor="feed-sort">
            Sort by
          </label>
          <Select
            id="feed-sort"
            label="Sort by"
            value={filters.sort || (personal ? "recommended" : "newest")}
            onChange={(sort) => router.push(hrefFor(filters, { sort }))}
            options={
              personal
                ? [
                    { value: "recommended", label: "Recommended" },
                    { value: "newest", label: "Newest" },
                    { value: "most_liked", label: "Most liked" },
                  ]
                : [
                    { value: "newest", label: "Newest" },
                    { value: "oldest", label: "Oldest" },
                    { value: "most_liked", label: "Most liked" },
                  ]
            }
          />
          {!personal && <ExtensionInstallButton />}
          {!sourcePage && (
            <ReaderDisclosure
              className="filter-menu"
              popover
              title={
                <>
                  <SlidersHorizontal size={17} />
                  Filters
                  {filterCount > 0 && <span className="filter-count">{filterCount}</span>}
                </>
              }
            >
              <form
                action={formUrl.pathname}
                className="filter-popover"
                onSubmit={(event) => {
                  event.preventDefault();
                  const values = Object.fromEntries(new FormData(event.currentTarget)) as Record<
                    string,
                    string
                  >;
                  router.push(
                    hrefFor(parseFilters({ ...formFilters, ...values }), {
                      source_slug: sourcePage
                        ? filters.source_slug
                        : sources.find((source) => source.id === values.source_id)?.slug,
                    }),
                  );
                }}
              >
                {[...formUrl.searchParams].map(([name, value]) => (
                  <input type="hidden" key={name} name={name} value={value} />
                ))}
                {!sourcePage && (
                  <>
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
                  </>
                )}
                <button className="button primary" type="submit">
                  Apply filters
                </button>
              </form>
            </ReaderDisclosure>
          )}
        </div>
      </div>
      {!!active.length && (
        <div className="active-filters">
          {active.map(([key, value]) => (
            <Link
              key={key}
              href={hrefFor(filters, { [key]: "" })}
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
            href={
              sourcePage
                ? hrefFor({
                    ...parseFilters({ source_id: filters.source_id }),
                    source_slug: filters.source_slug,
                  })
                : topicPage
                  ? hrefFor(parseFilters({ topic: filters.topic }))
                  : personal
                    ? "/"
                    : "/latest"
            }
            className="clear-filters"
          >
            Clear all
          </Link>
        </div>
      )}
    </>
  );
}
