"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Columns3,
  MoreHorizontal,
  Pencil,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import { DropdownMenu } from "radix-ui";
import { readerLoginLink, readerLocation, readerRequest } from "@/lib/reader-runtime";
import { cycleDialogFocus, restoreDialogFocus } from "@/lib/dialog-focus";
import { createColumnRequestQueue, type ColumnRequestQueue } from "@/lib/column-feed";
import { createReaderColumnId } from "@/lib/reader-column-id";
import {
  maxReaderColumns,
  readerColumnFeedKey,
  type ReaderColumn,
  type ReaderColumnFeed,
} from "@/lib/reader-preferences";
import { ColumnPreferencesProvider, useColumnPreferences } from "./column-preferences";
import type { ColumnWorkspaceScope } from "@/lib/user-mutations";
import { useSettingsToast } from "./settings-toast";
import { AccountGate, useUser } from "./user-account";
import { EngagementProvider } from "./article-engagement";
import { useArticleNavigation } from "./article-navigation";
import { ColumnFeed } from "./column-feed";
import { StarterColumns } from "./starter-columns";
import { FeedLayoutMenu } from "./feed-layout-menu";
import styles from "./columns-workspace.module.css";

type Kind = ReaderColumnFeed["kind"];
type CatalogKind = "source" | "topic" | "tag";
type Choice = { id: string; name: string; slug: string };
type Selection = { name: string; slug?: string };
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

function useSelection(
  feed: ReaderColumnFeed,
  attempt: number,
  queue?: ColumnRequestQueue,
): Resolved {
  const kind = feed.kind;
  const id = "id" in feed ? feed.id : "";
  const [result, setResult] = useState<{ key: string; value: Resolved } | null>(null);
  const key = `${kind}:${id}:${attempt}`;
  useEffect(() => {
    if (!isCatalog(kind)) return;
    const controller = new AbortController();
    const load = async (signal: AbortSignal) => {
      const response = await readerRequest(
        `/api/v1/${kind}s/${encodeURIComponent(id)}${kind === "source" ? "" : "?by_id=true"}`,
        { signal },
      );
      if (response.status === 404) return { status: "unavailable" } as const;
      if (!response.ok) throw new Error("Selection unavailable");
      const choice = (await response.json()) as Choice;
      if (choice.id !== id || !choice.name || !choice.slug) throw new Error("Invalid selection");
      return {
        status: "ready",
        selection: { name: choice.name, slug: choice.slug },
      } as const;
    };
    void (
      queue
        ? queue.run(load, controller.signal)
        : load(AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]))
    )
      .then((value) => {
        if (!controller.signal.aborted) setResult({ key, value });
      })
      .catch(() => {
        if (!controller.signal.aborted) setResult({ key, value: { status: "error" } });
      });
    return () => controller.abort();
  }, [kind, id, attempt, key, queue]);
  if (!isCatalog(kind))
    return {
      status: "ready",
      selection: { name: labels[kind] },
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
  queue,
  active,
  onActivate,
  onArticles,
  filtersKey,
}: {
  column: ReaderColumn;
  index: number;
  count: number;
  disabled: boolean;
  onEdit: () => void;
  onRemove: () => void;
  onMove: (direction: -1 | 1) => void;
  queue: ColumnRequestQueue;
  active: boolean;
  onActivate: () => void;
  onArticles: (id: string, articles: string[]) => void;
  filtersKey: string;
}) {
  const { user, loading: checking } = useUser();
  const [attempt, setAttempt] = useState(0);
  const result = useSelection(column.feed, attempt, queue);
  const name =
    result.status === "ready"
      ? result.selection.name
      : result.status === "loading"
        ? `Loading ${column.feed.kind}…`
        : `Unavailable ${column.feed.kind}`;
  const heading = useId();
  const options = useRef<HTMLButtonElement>(null);
  const editRequested = useRef(false);
  const articles = result.status === "ready" && (
    <ColumnFeed
      key={`${column.feed.kind}:${"id" in column.feed ? column.feed.id : ""}:${attempt}:${filtersKey}`}
      columnId={column.id}
      name={name}
      feed={column.feed}
      slug={result.selection.slug}
      queue={queue}
      active={active}
      onActivate={onActivate}
      onArticles={onArticles}
    />
  );
  return (
    <section
      className={styles.column}
      data-column-id={column.id}
      data-feed-kind={column.feed.kind}
      tabIndex={-1}
      aria-labelledby={heading}
    >
      <div className={styles.columnHeading}>
        <div>
          <h2 id={heading} title={name}>
            {name}
          </h2>
          {isCatalog(column.feed.kind) && (
            <span className={styles.kind}>{labels[column.feed.kind]}</span>
          )}
        </div>
        <div className={styles.headingActions}>
          <DropdownMenu.Root modal={false}>
            <DropdownMenu.Trigger asChild>
              <button
                ref={options}
                className={styles.iconButton}
                aria-label={`Options for ${name} column`}
                disabled={disabled}
              >
                <MoreHorizontal size={19} aria-hidden="true" />
              </button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content
                className={styles.columnMenu}
                align="end"
                sideOffset={6}
                collisionPadding={12}
                onCloseAutoFocus={(event) => {
                  if (editRequested.current) {
                    event.preventDefault();
                    editRequested.current = false;
                    options.current?.focus();
                    onEdit();
                  } else if (document.activeElement?.hasAttribute("data-column-id")) {
                    event.preventDefault();
                  }
                }}
              >
                <DropdownMenu.Item
                  disabled={disabled}
                  aria-label={`Edit ${name} column`}
                  onSelect={() => {
                    editRequested.current = true;
                  }}
                >
                  <Pencil size={16} aria-hidden="true" /> Change feed
                </DropdownMenu.Item>
                <DropdownMenu.Item
                  aria-label={`Move ${name} left`}
                  disabled={disabled || index === 0}
                  onSelect={() => onMove(-1)}
                >
                  <ArrowLeft size={16} aria-hidden="true" /> Move left
                </DropdownMenu.Item>
                <DropdownMenu.Item
                  aria-label={`Move ${name} right`}
                  disabled={disabled || index === count - 1}
                  onSelect={() => onMove(1)}
                >
                  <ArrowRight size={16} aria-hidden="true" /> Move right
                </DropdownMenu.Item>
                <DropdownMenu.Separator />
                <DropdownMenu.Item
                  disabled={disabled}
                  className={styles.removeItem}
                  aria-label={`Remove ${name} column`}
                  onSelect={onRemove}
                >
                  <Trash2 size={16} aria-hidden="true" /> Remove column
                </DropdownMenu.Item>
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>
          <button
            type="button"
            className={`${styles.iconButton} ${styles.removeButton}`}
            aria-label={`Remove ${name} column`}
            title={`Remove ${name} column`}
            disabled={disabled}
            onClick={onRemove}
          >
            <X size={17} aria-hidden="true" />
          </button>
        </div>
      </div>
      {result.status === "ready" ? (
        <div className={styles.feedSlot}>
          {column.feed.kind === "recommended" ? (
            user ? (
              <AccountGate
                loadingFallback={
                  <p className={styles.feedStatus} role="status">
                    Checking your account…
                  </p>
                }
              >
                {articles}
              </AccountGate>
            ) : (
              <div className={styles.columnBody}>
                <p role="status">
                  {checking ? "Checking your account…" : "Sign in to view recommendations."}
                </p>
                {!checking && (
                  <a
                    className="button"
                    {...readerLoginLink(readerLocation().pathname + readerLocation().search)}
                  >
                    Sign in
                  </a>
                )}
              </div>
            )
          ) : (
            articles
          )}
        </div>
      ) : (
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
          ) : null}
          {result.status === "unavailable" && (
            <button className="button" disabled={disabled} onClick={onEdit}>
              Choose another feed
            </button>
          )}
        </div>
      )}
    </section>
  );
}

function ColumnEditor({
  column,
  columns,
  busy,
  error,
  onSave,
  onClose,
  queue,
}: {
  column: ReaderColumn | null;
  columns: ReaderColumn[];
  busy: boolean;
  error: string;
  onSave: (feed: ReaderColumnFeed) => Promise<void>;
  onClose: () => void;
  queue: ColumnRequestQueue;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const used = new Set(
    columns.filter((item) => item.id !== column?.id).map((item) => readerColumnFeedKey(item.feed)),
  );
  const [kind, setKind] = useState<Kind>(
    column?.feed.kind ??
      (!used.has("latest") ? "latest" : !used.has("recommended") ? "recommended" : "topic"),
  );
  const [choice, setChoice] = useState<Choice | null>(null);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [results, setResults] = useState<{ key: string; items: Choice[]; error: boolean } | null>(
    null,
  );
  const heading = useId();
  const previous = useRef<HTMLElement | null>(null);
  const initial = useSelection(column?.feed ?? { kind: "latest" }, 0, queue);
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
    void queue
      .run(async (signal) => {
        const response = await readerRequest(`/api/v1/${kind}s?${params}`, { signal });
        if (!response.ok) throw new Error("Catalog unavailable");
        const value = (await response.json()) as { items: Choice[] };
        if (!Array.isArray(value.items)) throw new Error("Invalid catalog");
        if (!controller.signal.aborted)
          setResults({ key: searchKey, items: value.items.slice(0, 60), error: false });
      }, controller.signal)
      .catch(() => {
        if (!controller.signal.aborted) setResults({ key: searchKey, items: [], error: true });
      });
    return () => controller.abort();
  }, [kind, search, attempt, searchKey, queue]);
  const choices = results?.key === searchKey ? results : null;
  const selected =
    choice?.id ?? (column?.feed.kind === kind && "id" in column.feed ? column.feed.id : "");
  const unchanged =
    column &&
    column.feed.kind === kind &&
    (!isCatalog(kind) || ("id" in column.feed && column.feed.id === selected));
  const duplicate = used.has(
    readerColumnFeedKey(isCatalog(kind) ? { kind, id: selected } : { kind }),
  );
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
          if (busy || unchanged || duplicate) return;
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
              <option
                key={value}
                value={value}
                disabled={!isCatalog(value as Kind) && used.has(value)}
              >
                {label}
                {!isCatalog(value as Kind) && used.has(value) ? " · Added" : ""}
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
                    aria-label={item.name}
                    disabled={busy || used.has(readerColumnFeedKey({ kind, id: item.id }))}
                    onClick={() => setChoice(item)}
                  >
                    {item.name}
                    {used.has(readerColumnFeedKey({ kind, id: item.id })) && (
                      <span className={styles.added}>Added</span>
                    )}
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
        {duplicate && (
          <p className={styles.error} role="alert">
            This feed is already in your workspace.
          </p>
        )}
        <div className={styles.editorActions}>
          <button type="button" className="button" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button
            type="submit"
            className="button primary"
            disabled={busy || !!unchanged || duplicate || (isCatalog(kind) && !choice)}
          >
            {busy ? "Saving…" : "Save column"}
          </button>
        </div>
      </form>
    </dialog>
  );
}

export function ColumnsWorkspace({ workspace = "latest" }: { workspace?: ColumnWorkspaceScope }) {
  const { user, sessionRevision } = useUser();
  return (
    <ColumnPreferencesProvider
      key={`${user?.user_id ?? "guest"}:${sessionRevision}:${workspace}`}
      workspace={workspace}
    >
      <Workspace />
    </ColumnPreferencesProvider>
  );
}

function Workspace() {
  const {
    columns,
    content_types,
    languages,
    loading,
    busy,
    unavailable,
    error,
    setColumns,
    refresh,
    workspace,
  } = useColumnPreferences();
  const { loading: checking } = useUser();
  const notify = useSettingsToast();
  const { setSequence } = useArticleNavigation();
  const [queue] = useState(createColumnRequestQueue);
  const [articleIds, setArticleIds] = useState<Record<string, string[]>>({});
  const onArticles = useCallback((id: string, ids: string[]) => {
    setArticleIds((previous) => {
      const next = { ...previous };
      if (ids.length) next[id] = ids;
      else delete next[id];
      return next;
    });
  }, []);
  useEffect(() => () => setSequence(null), [setSequence]);
  const [editor, setEditor] = useState<{ id: string; column: ReaderColumn | null } | null>(null);
  const [active, setActive] = useState("");
  useEffect(() => {
    if (!columns.some((column) => column.id === active)) setSequence(null);
  }, [columns, active, setSequence]);
  const board = useRef<HTMLDivElement>(null);
  const addButton = useRef<HTMLButtonElement>(null);
  const focusAddAfterSave = useRef(false);
  useEffect(() => {
    const element = board.current;
    if (!element) return;
    const measure = () => {
      const main = element.closest("main");
      const bottom = main ? Number.parseFloat(getComputedStyle(main).paddingBottom) || 24 : 24;
      const padding = Number.parseFloat(getComputedStyle(element).paddingBottom) || 14;
      const top = element.getBoundingClientRect().top + window.scrollY;
      const height = Math.max(280, Math.floor(window.innerHeight - top - bottom - padding - 3));
      element.style.setProperty("--column-height", `${height}px`);
    };
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    if (element.parentElement) observer?.observe(element.parentElement);
    window.addEventListener("resize", measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [columns.length, loading]);
  const disabled = loading || busy || checking || unavailable;
  useEffect(() => {
    if (!disabled && focusAddAfterSave.current) {
      focusAddAfterSave.current = false;
      addButton.current?.focus();
    }
  }, [disabled]);
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
      else if (!next.length) {
        if (addButton.current && !addButton.current.disabled) addButton.current.focus();
        else focusAddAfterSave.current = true;
      }
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
          <h1>{workspace === "my_feed" ? "My feed" : "Latest"}</h1>
        </div>
        <div className={styles.workspaceActions}>
          <FeedLayoutMenu compact allowColumns />
          <button
            ref={addButton}
            className={`button ${styles.addColumn}`}
            disabled={disabled || columns.length >= maxReaderColumns}
            onClick={() => setEditor({ id: createReaderColumnId(), column: null })}
          >
            <Plus size={18} aria-hidden="true" />
            Add column
          </button>
        </div>
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
          {!columns.length ? (
            editor ? (
              <div className={styles.empty}>
                <Columns3 size={38} aria-hidden="true" />
                <h2>Build your reading workspace</h2>
                <p>Add Latest, Recommended, or a source, topic, or tag to get started.</p>
              </div>
            ) : (
              <StarterColumns queue={queue} />
            )
          ) : (
            <EngagementProvider
              articleIds={columns.flatMap((column) => articleIds[column.id] ?? [])}
            >
              <div
                ref={board}
                className={styles.board}
                role="region"
                aria-label="Column feeds"
                tabIndex={0}
              >
                {columns.map((column, index) => (
                  <ColumnCard
                    key={column.id}
                    column={column}
                    index={index}
                    count={columns.length}
                    disabled={disabled}
                    queue={queue}
                    active={active === column.id}
                    onActivate={() => setActive(column.id)}
                    onArticles={onArticles}
                    filtersKey={`${content_types.join(",")}:${languages.join(",")}`}
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
                      [next[index], next[index + direction]] = [
                        next[index + direction],
                        next[index],
                      ];
                      void persist(next, "Column moved.", column.id);
                    }}
                  />
                ))}
              </div>
            </EngagementProvider>
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
          columns={columns}
          queue={queue}
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
