"use client";
import Link from "next/link";
import { DatePicker } from "@devfeed/ui/date-picker";
import { Select } from "@devfeed/ui/select";
import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { searchHref, searchKinds, type SearchOptions } from "@/lib/search";

export function SearchFilters({ query, options }: { query: string; options: SearchOptions }) {
  const router = useRouter();
  const [section, setSection] = useState(options.section);
  const [sort, setSort] = useState(options.sort);
  const [from, setFrom] = useState(options.date_from);
  const [to, setTo] = useState(options.date_to);
  const [pending, startTransition] = useTransition();
  const today = new Date().toISOString().slice(0, 10);
  const articles = !section || section === "articles";
  return (
    <form
      className="search-filters"
      action="/search"
      aria-label="Search filters"
      aria-busy={pending}
      onSubmit={(event) => {
        event.preventDefault();
        const params = new URLSearchParams();
        for (const [key, value] of new FormData(event.currentTarget))
          if (typeof value === "string" && value) params.set(key, value);
        startTransition(() => router.push(`/search?${params}`, { scroll: false }));
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
          onChange={(value) => setSection(value as SearchOptions["section"])}
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
          onChange={(value) => setSort(value as SearchOptions["sort"])}
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
          onChange={setFrom}
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
          onChange={setTo}
        />
      </div>
      <button type="submit" className="button primary" disabled={pending}>
        {pending ? "Applying…" : "Apply"}
      </button>
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
