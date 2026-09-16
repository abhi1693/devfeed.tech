"use client";
import Link from "next/link";
import { DatePicker } from "@devfeed/ui/date-picker";
import { Select } from "@devfeed/ui/select";
import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { searchHref, searchKinds, type SearchOptions } from "@/lib/search";

export function SearchFilters({
  query,
  options,
  loading = false,
}: {
  query: string;
  options: SearchOptions;
  loading?: boolean;
}) {
  const router = useRouter();
  const [section, setSection] = useState(options.section);
  const [sort, setSort] = useState(options.sort);
  const [from, setFrom] = useState(options.date_from);
  const [to, setTo] = useState(options.date_to);
  const [pending, startTransition] = useTransition();
  const busy = pending || loading;
  const today = new Date().toISOString().slice(0, 10);
  const articles = !section || section === "articles";
  function apply(changes: Partial<SearchOptions>) {
    const next = { section, sort, date_from: from, date_to: to, ...changes };
    const params = new URLSearchParams({ q: query });
    if (next.section) params.set("section", next.section);
    if (!next.section || next.section === "articles") {
      params.set("sort", next.sort);
      if (next.date_from) params.set("date_from", next.date_from);
      if (next.date_to) params.set("date_to", next.date_to);
    }
    startTransition(() => router.push(`/search?${params}`, { scroll: false }));
  }
  return (
    <form
      className="search-filters"
      action="/search"
      aria-label="Search filters"
      aria-busy={busy}
      onSubmit={(event) => {
        event.preventDefault();
        apply({});
      }}
    >
      <input type="hidden" name="q" value={query} />
      <div className="search-filter-field">
        <label htmlFor="search-result-type">Results</label>
        <Select
          id="search-result-type"
          name="section"
          label="Results"
          value={section}
          onChange={(value) => {
            const nextSection = value as SearchOptions["section"];
            setSection(nextSection);
            apply({ section: nextSection });
          }}
          clearLabel="All results"
          placeholder="All results"
          options={searchKinds.map((kind) => ({
            value: kind,
            label: kind[0].toUpperCase() + kind.slice(1),
          }))}
        />
      </div>
      <div className="search-filter-field">
        <label htmlFor="search-article-order">Article order</label>
        <Select
          id="search-article-order"
          name="sort"
          label="Article order"
          value={sort}
          required
          disabled={!articles}
          onChange={(value) => {
            const nextSort = value as SearchOptions["sort"];
            setSort(nextSort);
            apply({ sort: nextSort });
          }}
          options={[
            { value: "relevance", label: "Most relevant" },
            { value: "newest", label: "Newest first" },
            { value: "oldest", label: "Oldest first" },
          ]}
        />
      </div>
      <div className="search-filter-field">
        <label htmlFor="search-date-from">Article date from</label>
        <DatePicker
          id="search-date-from"
          name="date_from"
          label="Article date from"
          value={from}
          max={to && to < today ? to : today}
          disabled={!articles}
          onChange={(value) => {
            setFrom(value);
            apply({ date_from: value });
          }}
        />
      </div>
      <div className="search-filter-field">
        <label htmlFor="search-date-to">To</label>
        <DatePicker
          id="search-date-to"
          name="date_to"
          label="To"
          value={to}
          min={from || undefined}
          max={today}
          disabled={!articles}
          onChange={(value) => {
            setTo(value);
            apply({ date_to: value });
          }}
        />
      </div>
      {busy && <p role="status">Updating results…</p>}
      {(options.section ||
        options.sort !== "relevance" ||
        options.date_from ||
        options.date_to) && (
        <Link className="button" href={searchHref(query)}>
          Clear filters
        </Link>
      )}
    </form>
  );
}
