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
  const selectionReady = resolved?.key === selectionKey;
  const selectedScope = selection ? (selectionReady ? resolved.scope : null) : scope;
  const waitingForSelection = !!selection && !selectedScope;
  const selectionUnavailable = !!selection && selectionReady && !selectedScope;
  const scoped = !!scope || !!selection;
  const content = (
    <FeedLayoutContent
      allowColumns={allowColumns}
      workspace={workspace}
      scoped={scoped}
      selectionUnavailable={selectionUnavailable}
      retrySelection={() => setAttempt((value) => value + 1)}
    >
      {children}
    </FeedLayoutContent>
  );
  return scoped ? (
    // Keep the provider tree mounted while an alias resolves without using a placeholder scope.
    <ScopedFeedPreferences scope={selectedScope ?? "latest"} paused={waitingForSelection}>
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
  selectionUnavailable,
  retrySelection,
}: {
  children: ReactNode;
  allowColumns: boolean;
  workspace: ColumnWorkspaceScope;
  scoped: boolean;
  selectionUnavailable: boolean;
  retrySelection: () => void;
}) {
  const { view, loading, unavailable, refresh } = useFeedPreferences();
  const { user } = useUser();
  if (scoped && loading && !selectionUnavailable && workspace === "my_feed" && user)
    return <LoadingSkeleton label="Loading feed layout…" />;
  return (
    <>
      {(selectionUnavailable || (scoped && unavailable)) && (
        <div className="profile-load-error" role="status">
          <p>Couldn’t load this feed’s layout.</p>
          <button className="button" onClick={selectionUnavailable ? retrySelection : refresh}>
            Try again
          </button>
        </div>
      )}
      {allowColumns &&
      !selectionUnavailable &&
      !(scoped && unavailable) &&
      workspace === "my_feed" &&
      user &&
      view === "columns" ? (
        <ColumnsWorkspace key={`${user.user_id}:${workspace}`} workspace={workspace} />
      ) : (
        children
      )}
    </>
  );
}
