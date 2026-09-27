"use client";
import { animateReader } from "@/lib/reader-motion";
import { readerLoginLink } from "@/lib/reader-runtime";
import type { ComponentProps } from "react";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import { Bookmark, Eye, Heart } from "lucide-react";
import { trackEvent } from "@/lib/analytics";
import { userRequest } from "@/lib/user";
import { MotionIcon } from "./motion-icon";
import { useUser } from "./user-account";

type Engagement = {
  article_id: string;
  likes: number;
  opens: number;
  liked: boolean;
  bookmarked?: boolean;
};
const Context = createContext<Record<string, Engagement>>({});
// Keep each request within the user API's engagement query limit.
const engagementBatchSize = 100;
const changed = "devfeed:article-engagement";
export const bookmarkChanged = "devfeed:article-bookmark";
export type BookmarkChange = { article_id: string; bookmarked: boolean; owner: string };
function publish(value: Engagement, owner: string | null) {
  window.dispatchEvent(new CustomEvent(changed, { detail: { value, owner } }));
}
const count = (value: number) =>
  new Intl.NumberFormat("en", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);

export function useArticleEngagement(articleId: string) {
  return useContext(Context)[articleId];
}

export function EngagementProvider(props: { articleIds: string[]; children: React.ReactNode }) {
  const { user, loading } = useUser();
  return (
    <ScopedEngagementProvider key={loading ? "loading" : (user?.user_id ?? "guest")} {...props} />
  );
}

function ScopedEngagementProvider({
  articleIds,
  children,
}: {
  articleIds: string[];
  children: React.ReactNode;
}) {
  const { user, loading } = useUser();
  const [values, setValues] = useState<Record<string, Engagement>>({});
  const ids = [...new Set(articleIds)].join(",");
  // This cache belongs to the keyed session and is never persisted to browser storage.
  const loaded = useRef(new Set<string>());
  const loader = useRef<{
    controller: AbortController;
    queued: Set<string>;
    pending: Set<string>;
    running: boolean;
  } | null>(null);
  const revision = useRef(0);
  const writes = useRef<Record<string, number>>({});
  const bookmarks = useRef<Record<string, { revision: number; bookmarked: boolean }>>({});
  useEffect(() => {
    const update = (event: Event) => {
      const { value, owner } = (event as CustomEvent<{ value: Engagement; owner: string | null }>)
        .detail;
      if (owner !== (user?.user_id ?? null)) return;
      writes.current[value.article_id] = ++revision.current;
      setValues((current) => ({
        ...current,
        [value.article_id]: {
          ...value,
          bookmarked: current[value.article_id]?.bookmarked ?? value.bookmarked,
        },
      }));
    };
    const bookmark = (event: Event) => {
      const value = (event as CustomEvent<BookmarkChange>).detail;
      if (value.owner !== user?.user_id) return;
      bookmarks.current[value.article_id] = {
        revision: ++revision.current,
        bookmarked: value.bookmarked,
      };
      setValues((current) =>
        current[value.article_id]
          ? {
              ...current,
              [value.article_id]: { ...current[value.article_id], bookmarked: value.bookmarked },
            }
          : current,
      );
    };
    window.addEventListener(changed, update);
    window.addEventListener(bookmarkChanged, bookmark);
    return () => {
      window.removeEventListener(changed, update);
      window.removeEventListener(bookmarkChanged, bookmark);
    };
  }, [user?.user_id]);
  useEffect(() => {
    const controller = new AbortController();
    loader.current = { controller, queued: new Set(), pending: new Set(), running: false };
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (loading || !ids) return;
    const queue = loader.current;
    if (!queue || queue.controller.signal.aborted) return;
    for (const id of ids.split(",")) {
      if (loaded.current.has(id) || queue.pending.has(id)) continue;
      queue.queued.add(id);
      queue.pending.add(id);
    }
    if (queue.running) return;
    queue.running = true;
    const { controller } = queue;
    const load = async () => {
      try {
        // Appending a page keeps in-flight work and adds only missing IDs to this queue.
        while (queue.queued.size) {
          if (controller.signal.aborted) return;
          const batch = [...queue.queued].slice(0, engagementBatchSize);
          batch.forEach((id) => queue.queued.delete(id));
          const started = revision.current;
          const query = new URLSearchParams();
          batch.forEach((id) => query.append("article_id", id));
          try {
            const items = await userRequest<Engagement[]>(`engagement?${query}`, {
              signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
            });
            if (controller.signal.aborted) return;
            for (const item of items) loaded.current.add(item.article_id);
            setValues((current) => {
              const next = { ...current };
              for (const item of items) {
                const value =
                  (writes.current[item.article_id] ?? 0) > started && current[item.article_id]
                    ? current[item.article_id]
                    : item;
                const bookmark = bookmarks.current[item.article_id];
                next[item.article_id] =
                  bookmark && bookmark.revision > started
                    ? { ...value, bookmarked: bookmark.bookmarked }
                    : value;
              }
              return next;
            });
          } catch {
            // Keep successful batches and continue. Missing records remain eligible for
            // another attempt when the feed changes; reading never waits for metrics.
            if (controller.signal.aborted) return;
          } finally {
            batch.forEach((id) => queue.pending.delete(id));
          }
        }
      } finally {
        queue.running = false;
      }
    };
    void load();
  }, [ids, user?.user_id, loading]);
  return <Context.Provider value={values}>{children}</Context.Provider>;
}

export function ArticleReadLink({
  articleId,
  children,
  ...props
}: ComponentProps<"a"> & { articleId: string }) {
  const { onClick, onAuxClick, ...anchorProps } = props;
  const { user, profile, refreshProfile } = useUser();
  function recordOpen() {
    trackEvent("article_open", { article_id: articleId });
    // Navigation never waits for telemetry. Keep the request alive if this tab leaves.
    void userRequest<Engagement>(`articles/${articleId}/open`, {
      method: "POST",
      keepalive: true,
      headers: user?.csrf_token ? { "X-CSRF-Token": user.csrf_token } : {},
    })
      .then((value) => {
        publish(value, user?.user_id ?? null);
        if (
          user &&
          profile?.reading_streak?.last_read_date !== new Date().toISOString().slice(0, 10)
        )
          refreshProfile();
      })
      .catch(() => {});
  }
  return (
    <a
      {...anchorProps}
      onClick={(event) => {
        recordOpen();
        onClick?.(event);
      }}
      onAuxClick={(event) => {
        if (event.button === 1) {
          recordOpen();
          onAuxClick?.(event);
        }
      }}
    >
      {children}
    </a>
  );
}

export function ArticleEngagement({
  articleId,
  articleSlug,
}: {
  articleId: string;
  articleSlug: string;
}) {
  const value = useArticleEngagement(articleId);
  const { user } = useUser();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const heartRef = useRef<HTMLSpanElement>(null);
  async function toggle() {
    if (!user || busy) return;
    setBusy(true);
    setFailed(false);
    try {
      const result = await userRequest<Engagement>(`articles/${articleId}/like`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": user.csrf_token,
        },
        body: JSON.stringify({ liked: !value?.liked }),
      });
      publish(result, user.user_id);
      if (result.liked && !value?.liked)
        animateReader(
          heartRef.current,
          [{ transform: "scale(1)" }, { transform: "scale(1.18)" }, { transform: "scale(1)" }],
          { duration: 180 },
        );
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }
  const likeCount = value ? `, ${count(value.likes)} likes` : "";
  const heart = (
    <>
      <span ref={heartRef} className="reader-motion-icon">
        <Heart size={16} fill={user && value?.liked ? "currentColor" : "none"} aria-hidden="true" />
      </span>
      {value && <span>{count(value.likes)}</span>}
    </>
  );
  return (
    <div className="article-engagement">
      {user ? (
        <button
          className={`heart-button ${value?.liked ? "liked" : ""}`}
          type="button"
          disabled={busy || !value}
          aria-label={`${value?.liked ? "Unlike article" : "Like article"}${likeCount}`}
          aria-pressed={!!value?.liked}
          onClick={toggle}
        >
          {heart}
        </button>
      ) : (
        <a
          className="heart-button"
          {...readerLoginLink(`/articles/${articleSlug}`)}
          aria-label={`Sign in to like this article${likeCount}`}
          title="Sign in to like"
        >
          {heart}
        </a>
      )}
      {value && (
        <span
          className="open-count"
          title={`${value.opens} clicks to the original article`}
          aria-label={`${value.opens} clicks to the original article`}
        >
          <Eye size={15} aria-hidden="true" />
          {count(value.opens)}
        </span>
      )}
      {failed && (
        <span className="engagement-error" role="alert">
          Couldn’t save. Try again.
        </span>
      )}
    </div>
  );
}

export function ArticleBookmarkButton({
  articleId,
  articleSlug,
  label = false,
}: {
  articleId: string;
  articleSlug: string;
  label?: boolean;
}) {
  const values = useContext(Context);
  const value = values[articleId];
  const { user, loading } = useUser();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  async function toggle() {
    if (!user || busy || !value) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setMessage("");
    setError(false);
    try {
      const result = await userRequest<{ article_id: string; bookmarked: boolean }>(
        `articles/${articleId}/bookmark`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": user.csrf_token },
          body: JSON.stringify({ bookmarked: !value.bookmarked }),
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
        },
      );
      if (controller.signal.aborted) return;
      window.dispatchEvent(
        new CustomEvent(bookmarkChanged, {
          detail: { ...result, owner: user.user_id } satisfies BookmarkChange,
        }),
      );
      setMessage(result.bookmarked ? "Saved to Read later." : "Removed from Read later.");
    } catch {
      if (!controller.signal.aborted) {
        setError(true);
        setMessage("Couldn’t update bookmark. Try again.");
      }
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  const saved = !!user && !!value?.bookmarked;
  const icon = (
    <MotionIcon value={saved ? "saved" : "unsaved"}>
      <Bookmark size={16} fill={saved ? "currentColor" : "none"} />
    </MotionIcon>
  );
  return (
    <span className="article-bookmark">
      {!loading && !user ? (
        <a
          className="bookmark-button"
          {...readerLoginLink(`/articles/${articleSlug}`)}
          aria-label="Sign in to save article for later"
          title="Sign in to save for later"
        >
          {icon}
          {label && <span className="bookmark-label">{saved ? "Saved" : "Bookmark"}</span>}
        </a>
      ) : (
        <button
          className="bookmark-button"
          type="button"
          disabled={loading || busy || !value}
          aria-busy={busy}
          aria-pressed={saved}
          aria-label={saved ? "Remove bookmark" : "Save article for later"}
          title={saved ? "Remove from Read later" : "Save for later"}
          onClick={toggle}
        >
          {icon}
          {label && <span className="bookmark-label">{saved ? "Saved" : "Bookmark"}</span>}
        </button>
      )}
      <span className={error ? "bookmark-error" : "sr-only"} role={error ? "alert" : "status"}>
        {message}
      </span>
    </span>
  );
}
