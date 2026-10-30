"use client";

import "../app/styles/tags.css";
import { Hash, Search } from "lucide-react";
import { useCallback, useState } from "react";
import { InfiniteScroll } from "./infinite-scroll";
import { readerRequest } from "@/lib/reader-runtime";
import { flattenPageItems, uniquePageItems, useInfinitePages } from "@/lib/use-infinite-pages";
import { catalogHref, type CatalogPage } from "@/lib/catalog-page";
import Link from "./reader-link";
import { useReaderRouter } from "@/lib/reader-navigation";
import type { Tag } from "@/lib/types";

const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("");
export function tagLetter(name: string) {
  const first = name.charAt(0).toUpperCase();
  return alphabet.includes(first) ? first : "#";
}
export function tagsHref(query: string, letter: string, offset = 0) {
  return catalogHref("tags", { query, letter, offset });
}
export function TagsContent({
  tags,
  query = "",
  letter = "",
  nextCursor,
  offset = 0,
}: {
  tags: Tag[];
  query?: string;
  letter?: string;
  nextCursor: string | null;
  offset?: number;
}) {
  const router = useReaderRouter();
  const [search, setSearch] = useState(query);
  const fetchPage = useCallback(
    async (cursor: string, signal: AbortSignal) => {
      const params = new URLSearchParams({ offset: cursor, q: query, letter });
      const response = await readerRequest(`/api/v1/tags?${params}`, { signal, cache: "no-store" });
      if (!response.ok) throw new Error("Couldn’t load more tags.");
      return (await response.json()) as CatalogPage<Tag>;
    },
    [query, letter],
  );
  const { pages, cursor, loading, error, loadMore } = useInfinitePages(
    { items: tags, next_cursor: nextCursor },
    fetchPage,
  );
  const items = uniquePageItems(flattenPageItems(pages), (tag) => tag.id);

  const groups = [...alphabet, "#"]
    .map((initial) => ({ initial, tags: items.filter((tag) => tagLetter(tag.name) === initial) }))
    .filter((group) => group.tags.length);
  return (
    <div className="tags-directory">
      <header className="tags-hero">
        <span className="tags-eyebrow">
          <Hash size={16} aria-hidden="true" /> FIND YOUR NEXT INTEREST
        </span>
        <h1>Explore tags</h1>
        <p>
          From the tools you use to the ideas you’re curious about. Find a tag and explore what
          developers are reading.
        </p>
        <form
          className="tags-search"
          role="search"
          onSubmit={(event) => {
            event.preventDefault();
            router.push(tagsHref(search.trim().slice(0, 200), letter));
          }}
        >
          <Search size={20} aria-hidden="true" />
          <input
            aria-label="Search all tags"
            placeholder="Search all tags"
            value={search}
            maxLength={200}
            onChange={(event) => setSearch(event.target.value)}
          />
          <button className="button" type="submit">
            Search
          </button>
        </form>
      </header>
      <nav className="tags-alphabet" aria-label="Filter tags by letter">
        {["", ...alphabet, "#"].map((initial) => (
          <Link
            key={initial}
            href={tagsHref(query, initial)}
            aria-current={letter === initial ? "page" : undefined}
          >
            {initial || "All"}
          </Link>
        ))}
      </nav>
      <InfiniteScroll
        hasMore={cursor !== null}
        loading={loading}
        error={!!error}
        onLoadMore={loadMore}
        label="tags"
        endMessage={items.length ? "You’ve seen all matching tags." : ""}
      >
        {groups.map((group) => (
          <section className="tags-group" key={group.initial} aria-label={`Tags: ${group.initial}`}>
            <h3>
              <span>{group.initial}</span>
            </h3>
            <ul>
              {group.tags.map((tag) => (
                <li key={tag.id}>
                  <Link href={`/tags/${encodeURIComponent(tag.slug)}`}>
                    <span className="tags-hash" aria-hidden="true">
                      #
                    </span>
                    {tag.name}
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        ))}
        {cursor !== null && (
          <Link
            className="button"
            rel="next"
            href={catalogHref("tags", { query, letter, offset: cursor })}
          >
            Next tags
          </Link>
        )}
        {!items.length && (
          <section className="empty-state">
            <Hash size={32} aria-hidden="true" />
            <h2>No tags found</h2>
            <p>Try another search or browse all tags.</p>
            <Link className="button" href="/tags">
              Browse all tags
            </Link>
          </section>
        )}
      </InfiniteScroll>
      {offset > 0 && (
        <nav className="pagination" aria-label="Tag pages">
          <Link className="button" href={tagsHref(query, letter, Math.max(0, offset - 60))}>
            Previous tags
          </Link>
        </nav>
      )}
    </div>
  );
}
