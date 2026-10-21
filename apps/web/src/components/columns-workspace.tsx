"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Columns3, Pencil, Plus, Trash2 } from "lucide-react";
import { readerLoginLink, readerLocation, readerRequest } from "@/lib/reader-runtime";
import { useReaderRouter } from "@/lib/reader-navigation";
import { cycleDialogFocus, restoreDialogFocus } from "@/lib/dialog-focus";
import { sourceHref } from "@/lib/feed-query";
import {
  maxReaderColumns,
  type ReaderColumn,
  type ReaderColumnFeed,
} from "@/lib/reader-preferences";
import { useFeedPreferences } from "./feed-preferences";
import { useSettingsToast } from "./settings-toast";
import { useUser } from "./user-account";
import styles from "./columns-workspace.module.css";

type Kind = ReaderColumnFeed["kind"];
type CatalogKind = "source" | "topic" | "tag";
type Choice = { id: string; name: string; slug: string };
type Selection = { name: string; href: string };
type Resolved =
  | { status: "loading" }
  | { status: "unavailable" }
  | { status: "error" }
  | { status: "ready"; selection: Selection };
const labels: Record<Kind, string> = {
  latest: "Latest",
  recommended: "Recommended",
  source: "Source",
  topic: "Topic",
  tag: "Tag",
};

function isCatalog(kind: Kind): kind is CatalogKind {
  return kind === "source" || kind === "topic" || kind === "tag";
}

function selectionHref(kind: CatalogKind, choice: Choice) {
  return kind === "source" ? sourceHref(choice) : `/${kind}s/${encodeURIComponent(choice.slug)}`;
}

function useSelection(feed: ReaderColumnFeed, attempt: number): Resolved {
  const kind = feed.kind;
  const id = "id" in feed ? feed.id : "";
  const [result, setResult] = useState<{ key: string; value: Resolved } | null>(null);
  const key = `${kind}:${id}:${attempt}`;
  useEffect(() => {
    if (!isCatalog(kind)) return;
    const controller = new AbortController();
    void readerRequest(
      `/api/v1/${kind}s/${encodeURIComponent(id)}${kind === "source" ? "" : "?by_id=true"}`,
      { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]) },
    )
      .then(async (response) => {
        if (response.status === 404) return { status: "unavailable" } as const;
        if (!response.ok) throw new Error("Selection unavailable");
        const choice = (await response.json()) as Choice;
        if (choice.id !== id || !choice.name || !choice.slug) throw new Error("Invalid selection");
        return {
          status: "ready",
          selection: { name: choice.name, href: selectionHref(kind, choice) },
        } as const;
      })
      .then((value) => {
        if (!controller.signal.aborted) setResult({ key, value });
      })
      .catch(() => {
        if (!controller.signal.aborted) setResult({ key, value: { status: "error" } });
      });
    return () => controller.abort();
  }, [kind, id, attempt, key]);
  if (!isCatalog(kind))
    return {
      status: "ready",
      selection: { name: labels[kind], href: kind === "latest" ? "/latest" : "/" },
    };
  return result?.key === key ? result.value : { status: "loading" };
}

function ColumnCard({
  column,
  index,
  count,
  disabled,
  onEdit,
  onRemove,
  onMove,
  onOpen,
  onName,
}: {
  column: ReaderColumn;
  index: number;
  count: number;
  disabled: boolean;
  onEdit: () => void;
  onRemove: () => void;
  onMove: (direction: -1 | 1) => void;
  onOpen: (href: string) => void;
  onName: (id: string, name: string) => void;
}) {
  const { user } = useUser();
  const [attempt, setAttempt] = useState(0);
  const result = useSelection(column.feed, attempt);
  const name =
    result.status === "ready"
      ? result.selection.name
      : result.status === "loading"
        ? `Loading ${column.feed.kind}…`
        : `Unavailable ${column.feed.kind}`;
  const heading = useId();
  useEffect(() => onName(column.id, name), [column.id, name, onName]);
  return (
    <section
      className={styles.column}
      data-column-id={column.id}
      tabIndex={-1}
      aria-labelledby={heading}
    >
      <div className={styles.columnHeading}>
        <div>
          <span className={styles.kind}>{labels[column.feed.kind]}</span>
          <h2 id={heading}>{name}</h2>
        </div>
        <button
          className={styles.iconButton}
          aria-label={`Edit ${name} column`}
          disabled={disabled}
          onClick={onEdit}
        >
          <Pencil size={17} aria-hidden="true" />
        </button>
      </div>
      <div className={styles.columnBody} aria-live="polite">
        {result.status === "loading" ? (
          <p>Checking this selection…</p>
        ) : result.status === "unavailable" ? (
          <p>
            This {column.feed.kind} is no longer available. Choose another feed or remove this
            column.
          </p>
        ) : result.status === "error" ? (
          <>
            <p>Couldn’t check this {column.feed.kind}.</p>
            <button className="button" onClick={() => setAttempt((value) => value + 1)}>
              Try again
            </button>
          </>
        ) : column.feed.kind === "recommended" && !user ? (
          <>
            <p>Sign in to view recommendations.</p>
            <a
              className="button"
              {...readerLoginLink(readerLocation().pathname + readerLocation().search)}
            >
              Sign in
            </a>
          </>
        ) : (
          <>
            <p>
              {column.feed.kind === "recommended"
                ? "Recommendations based on your interests."
                : "Open this feed to browse its articles."}
            </p>
            <button
              className="button"
              disabled={disabled}
              aria-label={`Open ${name} feed`}
              onClick={() => onOpen(result.selection.href)}
            >
              Open feed
            </button>
          </>
        )}
        {result.status === "unavailable" && (
          <button className="button" disabled={disabled} onClick={onEdit}>
            Choose another feed
          </button>
        )}
      </div>
      <div className={styles.columnActions}>
        <button
          className={styles.iconButton}
          aria-label={`Move ${name} left`}
          disabled={disabled || index === 0}
          onClick={() => onMove(-1)}
        >
          <ArrowLeft size={18} aria-hidden="true" />
        </button>
        <button
          className={styles.iconButton}
          aria-label={`Move ${name} right`}
          disabled={disabled || index === count - 1}
          onClick={() => onMove(1)}
        >
          <ArrowRight size={18} aria-hidden="true" />
        </button>
        <span>
          {index + 1} of {count}
        </span>
        <button
          className={styles.iconButton}
          aria-label={`Remove ${name} column`}
          disabled={disabled}
          onClick={onRemove}
        >
          <Trash2 size={17} aria-hidden="true" />
        </button>
      </div>
    </section>
  );
}

function ColumnEditor({
  column,
  busy,
  error,
  onSave,
  onClose,
}: {
  column: ReaderColumn | null;
  busy: boolean;
  error: string;
  onSave: (feed: ReaderColumnFeed) => Promise<void>;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [kind, setKind] = useState<Kind>(column?.feed.kind ?? "latest");
  const [choice, setChoice] = useState<Choice | null>(null);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [results, setResults] = useState<{ key: string; items: Choice[]; error: boolean } | null>(
    null,
  );
  const heading = useId();
  const previous = useRef<HTMLElement | null>(null);
  const initial = useSelection(column?.feed ?? { kind: "latest" }, 0);
  const searchKey = `${kind}:${search}:${attempt}`;
  useEffect(() => {
    const element = dialog.current!;
    previous.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    element.showModal();
    return () => {
      element.close();
      if (previous.current) restoreDialogFocus(previous.current);
    };
  }, []);
  useEffect(() => {
    const timer = setTimeout(() => setSearch(query.trim().slice(0, 200)), 200);
    return () => clearTimeout(timer);
  }, [query]);
  useEffect(() => {
    if (!isCatalog(kind)) return;
    const controller = new AbortController();
    const params = new URLSearchParams({ offset: "0", limit: "20", q: search, sort: "name" });
    void readerRequest(`/api/v1/${kind}s?${params}`, {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("Catalog unavailable");
        const value = (await response.json()) as { items: Choice[] };
        if (!Array.isArray(value.items)) throw new Error("Invalid catalog");
        if (!controller.signal.aborted)
          setResults({ key: searchKey, items: value.items.slice(0, 60), error: false });
      })
      .catch(() => {
        if (!controller.signal.aborted) setResults({ key: searchKey, items: [], error: true });
      });
    return () => controller.abort();
  }, [kind, search, attempt, searchKey]);
  const choices = results?.key === searchKey ? results : null;
  const selected =
    choice?.id ?? (column?.feed.kind === kind && "id" in column.feed ? column.feed.id : "");
  const unchanged =
    column &&
    column.feed.kind === kind &&
    (!isCatalog(kind) || ("id" in column.feed && column.feed.id === selected));
  return (
    <dialog
      ref={dialog}
      className={styles.editor}
      aria-labelledby={heading}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
      onKeyDown={(event) => {
        if (
          event.key === "Tab" &&
          dialog.current &&
          cycleDialogFocus(dialog.current, event.shiftKey)
        )
          event.preventDefault();
      }}
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (busy || unchanged) return;
          if (isCatalog(kind)) {
            if (choice) void onSave({ kind, id: choice.id });
          } else void onSave({ kind });
        }}
      >
        <h2 id={heading}>{column ? "Edit column" : "Add column"}</h2>
        <label>
          Feed type
          <select
            value={kind}
            disabled={busy}
            onChange={(event) => {
              setKind(event.target.value as Kind);
              setChoice(null);
              setQuery("");
              setSearch("");
            }}
          >
            {Object.entries(labels).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        {isCatalog(kind) && (
          <>
            <label>
              Find a {kind}
              <input
                type="search"
                maxLength={200}
                value={query}
                disabled={busy}
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            {column?.feed.kind === kind && initial.status === "ready" && !choice && (
              <p>Current selection: {initial.selection.name}</p>
            )}
            <div className={styles.choices} aria-label={`Choose a ${kind}`} aria-busy={!choices}>
              {!choices ? (
                <p role="status">Loading {kind}s…</p>
              ) : choices.error ? (
                <div role="alert">
                  <p>Couldn’t load {kind}s.</p>
                  <button
                    type="button"
                    className="button"
                    onClick={() => setAttempt((value) => value + 1)}
                  >
                    Try again
                  </button>
                </div>
              ) : choices.items.length ? (
                choices.items.map((item) => (
                  <button
                    type="button"
                    key={item.id}
                    aria-pressed={selected === item.id}
                    disabled={busy}
                    onClick={() => setChoice(item)}
                  >
                    {item.name}
                  </button>
                ))
              ) : (
                <p>No {kind}s match your search.</p>
              )}
            </div>
            {!!choices?.items.length && (
              <p className={styles.hint}>Search by name to narrow your choices.</p>
            )}
          </>
        )}
        {kind === "recommended" && (
          <p>
            Recommended uses the signed-in reader’s interests. Guests can save this column and sign
            in when ready.
          </p>
        )}
        {error && (
          <p className={styles.error} role="alert">
            {error}
          </p>
        )}
        <div className={styles.editorActions}>
          <button type="button" className="button" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button
            type="submit"
            className="button primary"
            disabled={busy || !!unchanged || (isCatalog(kind) && !choice)}
          >
            {busy ? "Saving…" : "Save column"}
          </button>
        </div>
      </form>
    </dialog>
  );
}

export function ColumnsWorkspace() {
  const { user } = useUser();
  return <Workspace key={user?.user_id ?? "guest"} />;
}

function Workspace() {
  const { columns, loading, busy, unavailable, error, setColumns, setView, refresh } =
    useFeedPreferences();
  const { user, loading: checking } = useUser();
  const notify = useSettingsToast();
  const router = useReaderRouter();
  const [editor, setEditor] = useState<{ id: string; column: ReaderColumn | null } | null>(null);
  const [active, setActive] = useState("");
  const [names, setNames] = useState<Record<string, string>>({});
  const onName = useCallback(
    (id: string, name: string) =>
      setNames((previous) => (previous[id] === name ? previous : { ...previous, [id]: name })),
    [],
  );
  const board = useRef<HTMLDivElement>(null);
  const addButton = useRef<HTMLButtonElement>(null);
  const disabled = loading || busy || checking || unavailable;
  const focusColumn = (id: string) => {
    setActive(id);
    const target = board.current?.querySelector<HTMLElement>(`[data-column-id="${id}"]`);
    target?.focus({ preventScroll: true });
    target?.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "instant" });
  };
  async function persist(next: ReaderColumn[], message: string, focus?: string) {
    if (disabled) return false;
    const saved = await setColumns(next);
    if (saved) {
      notify(message);
      if (focus) focusColumn(focus);
      else if (!next.length) addButton.current?.focus();
    }
    return saved;
  }
  return (
    <section
      className={styles.workspace}
      aria-label="Columns workspace"
      aria-busy={loading || busy || checking}
    >
      <div className={styles.heading}>
        <div>
          <h1>
            <Columns3 size={24} aria-hidden="true" />
            Your columns
          </h1>
          <p>Arrange feeds side by side. Open a feed to browse its articles.</p>
        </div>
        <button
          ref={addButton}
          className="button primary"
          disabled={disabled || columns.length >= maxReaderColumns}
          onClick={() => setEditor({ id: crypto.randomUUID(), column: null })}
        >
          <Plus size={18} aria-hidden="true" />
          Add column
        </button>
      </div>
      {unavailable ? (
        <div role="status">
          <p>Couldn’t load your Columns workspace.</p>
          <button className="button" onClick={refresh}>
            Try again
          </button>
        </div>
      ) : loading ? (
        <p role="status">Loading your columns…</p>
      ) : (
        <>
          <div className={styles.toolbar}>
            <p>
              {columns.length} of {maxReaderColumns} columns
            </p>
            {!!columns.length && (
              <label>
                Go to column
                <select
                  value={columns.some((column) => column.id === active) ? active : ""}
                  onChange={(event) => focusColumn(event.target.value)}
                >
                  <option value="" disabled>
                    Choose a column
                  </option>
                  {columns.map((column, index) => (
                    <option key={column.id} value={column.id}>
                      {index + 1}. {names[column.id] ?? labels[column.feed.kind]}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
          {!columns.length ? (
            <div className={styles.empty}>
              <Columns3 size={38} aria-hidden="true" />
              <h2>Build your reading workspace</h2>
              <p>Add Latest, Recommended, or a source, topic, or tag to get started.</p>
            </div>
          ) : (
            <div ref={board} className={styles.board}>
              {columns.map((column, index) => (
                <ColumnCard
                  key={column.id}
                  column={column}
                  index={index}
                  count={columns.length}
                  disabled={disabled}
                  onName={onName}
                  onEdit={() => setEditor({ id: column.id, column })}
                  onRemove={() => {
                    void persist(
                      columns.filter((item) => item.id !== column.id),
                      "Column removed.",
                      columns[index + 1]?.id ?? columns[index - 1]?.id,
                    );
                  }}
                  onMove={(direction) => {
                    if (index + direction < 0 || index + direction >= columns.length) return;
                    const next = [...columns];
                    [next[index], next[index + direction]] = [next[index + direction], next[index]];
                    void persist(next, "Column moved.", column.id);
                  }}
                  onOpen={(href) => {
                    void setView("cards").then((saved) => {
                      if (saved || !user) {
                        if (!saved)
                          notify(
                            "Browser preferences could not be saved. Opened the feed for this tab.",
                            "error",
                          );
                        router.push(href);
                      }
                    });
                  }}
                />
              ))}
            </div>
          )}
        </>
      )}
      {error && !editor && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      {editor && (
        <ColumnEditor
          column={editor.column}
          busy={busy || checking}
          error={error}
          onClose={() => setEditor(null)}
          onSave={async (feed) => {
            const id = editor.id;
            // A denied guest-storage write can still apply in this tab; retry the same identity.
            const next = columns.some((column) => column.id === id)
              ? columns.map((column) => (column.id === id ? { id, feed } : column))
              : [...columns, { id, feed }];
            if (await persist(next, editor.column ? "Column updated." : "Column added."))
              setEditor(null);
          }}
        />
      )}
    </section>
  );
}
