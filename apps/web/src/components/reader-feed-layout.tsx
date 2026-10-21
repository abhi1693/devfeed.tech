"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useFeedPreferences } from "./feed-preferences";
import { ColumnsWorkspace } from "./columns-workspace";
import { useUser } from "./user-account";
import type { ColumnWorkspaceScope } from "@/lib/user-mutations";
import { parseFeedViewScope, type FeedViewScope } from "@/lib/feed-view-scope";
import { readerRequest } from "@/lib/reader-runtime";
import { ScopedFeedPreferences } from "./scoped-feed-preferences";
import { LoadingSkeleton } from "./loading-skeleton";
import { SettingsToastProvider } from "./settings-toast";

/** My feed alone can replace its article feed with the signed-in reader's workspace. */
export function ReaderFeedLayout({
  children,
  allowColumns = false,
  workspace = "latest",
  scope,
  selection,
}: {
  children: ReactNode;
  allowColumns?: boolean;
  workspace?: ColumnWorkspaceScope;
  scope?: FeedViewScope;
  selection?: { kind: "topics" | "tags"; slug: string };
}) {
  const [attempt, setAttempt] = useState(0);
  const [resolved, setResolved] = useState<{ key: string; scope: FeedViewScope | null } | null>(
    null,
  );
  const kind = selection?.kind;
  const slug = selection?.slug;
  const selectionKey = `${kind}:${slug}:${attempt}`;
  useEffect(() => {
    if (!kind || !slug) return;
    const controller = new AbortController();
    void readerRequest(`/api/v1/${kind}/${encodeURIComponent(slug)}`, {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("Feed identity unavailable");
        const value = (await response.json()) as { id?: unknown };
        const target = parseFeedViewScope(`${kind === "topics" ? "topic" : "tag"}:${value.id}`);
        if (!target) throw new Error("Invalid feed identity");
        if (!controller.signal.aborted) setResolved({ key: selectionKey, scope: target });
      })
      .catch(() => {
        if (!controller.signal.aborted) setResolved({ key: selectionKey, scope: null });
      });
    return () => controller.abort();
  }, [kind, slug, selectionKey]);
  if (selection) {
    if (resolved?.key !== selectionKey) return <LoadingSkeleton label="Loading feed layout…" />;
    if (!resolved.scope)
      return (
        <>
          <div className="profile-load-error" role="status">
            <p>Couldn’t load this feed’s layout.</p>
            <button className="button" onClick={() => setAttempt((value) => value + 1)}>
              Try again
            </button>
          </div>
          {children}
        </>
      );
    scope = resolved.scope;
  }
  const content = (
    <FeedLayoutContent allowColumns={allowColumns} workspace={workspace} scoped={!!scope}>
      {children}
    </FeedLayoutContent>
  );
  return scope ? (
    <ScopedFeedPreferences scope={scope}>
      <SettingsToastProvider>{content}</SettingsToastProvider>
    </ScopedFeedPreferences>
  ) : (
    content
  );
}

function FeedLayoutContent({
  children,
  allowColumns,
  workspace,
  scoped,
}: {
  children: ReactNode;
  allowColumns: boolean;
  workspace: ColumnWorkspaceScope;
  scoped: boolean;
}) {
  const { view, loading, unavailable, refresh } = useFeedPreferences();
  const { user } = useUser();
  if (scoped && loading) return <LoadingSkeleton label="Loading feed layout…" />;
  if (scoped && unavailable)
    return (
      <>
        <div className="profile-load-error" role="status">
          <p>Couldn’t load this feed’s layout.</p>
          <button className="button" onClick={refresh}>
            Try again
          </button>
        </div>
        {children}
      </>
    );
  return allowColumns && workspace === "my_feed" && user && view === "columns" ? (
    <ColumnsWorkspace key={`${user?.user_id ?? "guest"}:${workspace}`} workspace={workspace} />
  ) : (
    children
  );
}
