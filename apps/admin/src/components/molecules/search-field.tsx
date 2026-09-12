"use client";

import { useEffect, useRef, useState } from "react";
import { Search, X } from "lucide-react";
import { Input } from "@/components/atoms/input";
import { Button } from "@/components/atoms/button";
import { cn } from "@/lib/utils";

/** Query acknowledgements and paging do not reset an in-progress search. */
export function searchScope(query: string) {
  const params = new URLSearchParams(query);
  params.delete("q");
  params.delete("offset");
  params.sort();
  return params.toString();
}

type Props = {
  value: string;
  label: string;
  placeholder?: string;
  scopeKey?: string;
  className?: string;
  onSearch: (value: string) => void;
};

/** Debounce typed searches, retaining focus and edits made during navigation. */
export function SearchField({
  value,
  label,
  placeholder = `${label}…`,
  scopeKey = "",
  className,
  onSearch,
}: Props) {
  const [draft, setDraft] = useState(value);
  const input = useRef<HTMLInputElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const composing = useRef(false);
  const sent = useRef<string[]>([]);
  const applied = useRef({ value, scopeKey });
  const latest = useRef(onSearch);
  useEffect(() => {
    latest.current = onSearch;
  }, [onSearch]);
  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => {
    const previous = applied.current;
    applied.current = { value, scopeKey };
    if (previous.value === value && previous.scopeKey === scopeKey) return;
    const acknowledgement = previous.scopeKey === scopeKey ? sent.current.indexOf(value) : -1;
    if (acknowledgement >= 0) {
      sent.current.splice(0, acknowledgement + 1);
      return;
    }
    // Back/forward, clearing filters or changing scope cancels any unsent text.
    clearTimeout(timer.current);
    sent.current = [];
    composing.current = false;
    setDraft(value);
  }, [value, scopeKey]);

  function submit(next: string) {
    clearTimeout(timer.current);
    const query = next.trim();
    if (
      composing.current ||
      (query === applied.current.value && !sent.current.length) ||
      query === sent.current.at(-1)
    )
      return;
    sent.current.push(query);
    latest.current(query);
  }
  function change(next: string) {
    setDraft(next);
    clearTimeout(timer.current);
    if (!composing.current) timer.current = setTimeout(() => submit(next), 250);
  }
  return (
    <form
      role="search"
      aria-label={label}
      className={cn("relative min-w-0", className)}
      onSubmit={(event) => {
        event.preventDefault();
        submit(draft);
      }}
    >
      <Search
        aria-hidden
        className="pointer-events-none absolute top-2.5 left-3 size-4 text-muted-foreground"
      />
      <Input
        ref={input}
        name="q"
        aria-label={label}
        placeholder={placeholder}
        value={draft}
        maxLength={200}
        className="pr-9 pl-9"
        onChange={(event) => change(event.target.value)}
        onCompositionStart={() => {
          composing.current = true;
          clearTimeout(timer.current);
        }}
        onCompositionEnd={(event) => {
          composing.current = false;
          change(event.currentTarget.value);
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter" && event.nativeEvent.isComposing) event.preventDefault();
        }}
      />
      {draft && (
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          className="absolute top-1.5 right-1.5"
          aria-label={`Clear ${label.toLowerCase()}`}
          onClick={() => {
            composing.current = false;
            setDraft("");
            submit("");
            input.current?.focus();
          }}
        >
          <X aria-hidden />
        </Button>
      )}
    </form>
  );
}
