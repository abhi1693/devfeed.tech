"use client";
import { animateReader } from "@/lib/reader-motion";
import { readerWebsiteLink } from "@/lib/reader-runtime";
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
  const ids = articleIds.join(",");
  const writes = useRef<Record<string, number>>({});
  useEffect(() => {
    const update = (event: Event) => {
      const { value, owner } = (event as CustomEvent<{ value: Engagement; owner: string | null }>)
        .detail;
      if (owner !== (user?.user_id ?? null)) return;
      writes.current[value.article_id] = Date.now();
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
      writes.current[value.article_id] = Date.now();
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
    if (loading || !ids) return;
    const controller = new AbortController();
    const started = Date.now();
    const query = new URLSearchParams();
    ids.split(",").forEach((id) => query.append("article_id", id));
    userRequest<Engagement[]>(`engagement?${query}`, {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
    })
      .then((items) =>
        setValues((current) =>
          Object.fromEntries(
            items.map((item) => [
              item.article_id,
              (writes.current[item.article_id] ?? 0) >= started && current[item.article_id]
                ? current[item.article_id]
                : item,
            ]),
          ),
        ),
      )
      .catch(() => {
        /* Article reading remains available if metrics are unavailable. */
      });
    return () => controller.abort();
  }, [ids, user?.user_id, loading]);
  return <Context.Provider value={values}>{children}</Context.Provider>;
}

export function ArticleReadLink({
  articleId,
  children,
  ...props
}: ComponentProps<"a"> & { articleId: string }) {
  const { user } = useUser();
  function recordOpen() {
    trackEvent("article_open", { article_id: articleId });
    // Navigation never waits for telemetry. Keep the request alive if this tab leaves.
    void userRequest<Engagement>(`articles/${articleId}/open`, {
      method: "POST",
      keepalive: true,
      headers: user?.csrf_token ? { "X-CSRF-Token": user.csrf_token } : {},
    })
      .then((value) => publish(value, user?.user_id ?? null))
      .catch(() => {});
  }
  return (
    <a
      {...props}
      onClick={recordOpen}
      onAuxClick={(event) => {
        if (event.button === 1) recordOpen();
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
  const values = useContext(Context);
  const value = values[articleId];
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
          {...readerWebsiteLink(
            `/api/v1/user/auth/login?return_to=${encodeURIComponent(`/articles/${articleSlug}`)}`,
          )}
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
          {...readerWebsiteLink(
            `/api/v1/user/auth/login?return_to=${encodeURIComponent(`/articles/${articleSlug}`)}`,
          )}
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
