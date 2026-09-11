"use client";

import { useEffect, useRef } from "react";
import { Search } from "lucide-react";
import { feedParams, type FeedFilters } from "@/lib/feed-query";

export function UserSearch({ filters }: { filters?: FeedFilters }) {
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const focusSearch = (event: KeyboardEvent) => {
      if (event.key !== "/" || event.defaultPrevented || event.isComposing || event.repeat || event.ctrlKey || event.metaKey || event.altKey) return;
      // Preserve typing, editor shortcuts, and focus within open overlays.
      const protectedTarget = event.composedPath().some(target => target instanceof Element && target.closest(
        'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"], [role="searchbox"], [role="combobox"], [role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"]',
      ));
      if (protectedTarget || document.querySelector("dialog[open]") || !input.current || input.current.disabled || input.current.closest('[inert], [aria-hidden="true"]')) return;
      event.preventDefault();
      input.current.focus();
    };
    document.addEventListener("keydown", focusSearch);
    return () => document.removeEventListener("keydown", focusSearch);
  }, []);

  return <form action="/" className="search" role="search">
    <Search size={20} aria-hidden="true" />
    <label className="sr-only" htmlFor="search">Search articles</label>
    <input ref={input} id="search" type="search" name="q" placeholder="Search developer articles" defaultValue={filters?.q} maxLength={200} aria-keyshortcuts="/" />
    {filters && [...feedParams({ ...filters, q: "", cursor: "" })].map(([name, value]) => <input key={name} type="hidden" name={name} value={value} />)}
    <kbd className="search-shortcut" title="Press / to search" aria-hidden="true">/</kbd>
  </form>;
}
