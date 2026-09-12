"use client";

import { useCallback, useEffect, useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Search } from "lucide-react";
import { feedHref, parseFilters, type FeedFilters } from "@/lib/feed-query";

type SearchFocus = { draft: string; start: number; end: number; submitted: string; path: string };
// The shell remounts when searching from another route. Carry only the active
// search's draft and caret across that navigation, never into storage or the URL.
let pendingSearchFocus: SearchFocus | null = null;

export function UserSearch({ filters }: { filters?: FeedFilters }) {
  const input = useRef<HTMLInputElement>(null);
  const composing = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const [initialFocus] = useState(() => pendingSearchFocus);
  const restored = useRef(false);
  const currentQuery = filters?.q ?? "";
  const navigate = useCallback(
    (value: string) => {
      const field = input.current;
      if (value.trim() === currentQuery && !pendingSearchFocus) return;
      const href = feedHref({ ...parseFilters({}), ...filters, q: value.trim(), cursor: "" });
      pendingSearchFocus =
        field && document.activeElement === field
          ? {
              draft: field.value,
              start: field.selectionStart ?? field.value.length,
              end: field.selectionEnd ?? field.value.length,
              submitted: value.trim(),
              path: new URL(href, window.location.origin).pathname,
            }
          : null;
      startTransition(() => router.replace(href, { scroll: false }));
    },
    [currentQuery, filters, router],
  );
  const schedule = useCallback(
    (value: string) => {
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        timer.current = null;
        navigate(value);
      }, 350);
    },
    [navigate],
  );
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  useEffect(() => {
    const field = input.current;
    if (restored.current || !initialFocus || !field) return;
    restored.current = true;
    if (
      window.location.pathname !== initialFocus.path ||
      new URLSearchParams(window.location.search).get("q") !== (initialFocus.submitted || null)
    )
      return;
    field.value = initialFocus.draft;
    field.focus();
    field.setSelectionRange(initialFocus.start, initialFocus.end);
    pendingSearchFocus = null;
    if (field.value.trim() !== currentQuery) schedule(field.value);
  }, [initialFocus, currentQuery, schedule]);
  useEffect(() => {
    if (pendingSearchFocus?.submitted === currentQuery) pendingSearchFocus = null;
    if (input.current && document.activeElement !== input.current)
      input.current.value = currentQuery;
  }, [currentQuery]);
  useEffect(() => {
    const focusSearch = (event: KeyboardEvent) => {
      if (
        event.key !== "/" ||
        event.defaultPrevented ||
        event.isComposing ||
        event.repeat ||
        event.ctrlKey ||
        event.metaKey ||
        event.altKey
      )
        return;
      // Preserve typing, editor shortcuts, and focus within open overlays.
      const protectedTarget = event
        .composedPath()
        .some(
          (target) =>
            target instanceof Element &&
            target.closest(
              'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"], [role="searchbox"], [role="combobox"], [role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"]',
            ),
        );
      if (
        protectedTarget ||
        document.querySelector("dialog[open]") ||
        !input.current ||
        input.current.disabled ||
        input.current.closest('[inert], [aria-hidden="true"]')
      )
        return;
      event.preventDefault();
      input.current.focus();
      const end = input.current.value.length;
      input.current.setSelectionRange(end, end);
    };
    document.addEventListener("keydown", focusSearch);
    return () => document.removeEventListener("keydown", focusSearch);
  }, []);

  const formUrl = new URL(
    feedHref({ ...parseFilters({}), ...filters, q: "", cursor: "" }),
    "http://localhost",
  );
  return (
    <form
      action={formUrl.pathname}
      className="search"
      role="search"
      aria-busy={isPending}
      onSubmit={(event) => {
        event.preventDefault();
        if (composing.current) return;
        if (timer.current) clearTimeout(timer.current);
        navigate(input.current?.value ?? "");
      }}
    >
      <Search size={20} aria-hidden="true" />
      <label className="sr-only" htmlFor="search">
        Search articles
      </label>
      <input
        ref={input}
        id="search"
        type="search"
        name="q"
        autoComplete="off"
        placeholder="Search developer articles"
        defaultValue={filters?.q}
        maxLength={200}
        aria-keyshortcuts="/"
        onChange={(event) => {
          const field = event.currentTarget;
          if (pendingSearchFocus) {
            pendingSearchFocus.draft = field.value;
            pendingSearchFocus.start = field.selectionStart ?? field.value.length;
            pendingSearchFocus.end = field.selectionEnd ?? field.value.length;
          }
          if (!composing.current) schedule(field.value);
        }}
        onCompositionStart={() => {
          composing.current = true;
          if (timer.current) clearTimeout(timer.current);
        }}
        onCompositionEnd={(event) => {
          composing.current = false;
          schedule(event.currentTarget.value);
        }}
        onBlur={() => {
          pendingSearchFocus = null;
        }}
      />
      {[...formUrl.searchParams].map(([name, value]) => (
        <input key={name} type="hidden" name={name} value={value} />
      ))}
      <span className="sr-only" role="status">
        {isPending ? "Updating search results…" : ""}
      </span>
      <kbd className="search-shortcut" title="Press / to search" aria-hidden="true">
        /
      </kbd>
    </form>
  );
}
