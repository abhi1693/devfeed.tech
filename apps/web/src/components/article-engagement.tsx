"use client";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import { Eye, Heart } from "lucide-react";
import { userRequest } from "@/lib/user";
import { useUser } from "./user-account";

type Engagement = {
  article_id: string;
  likes: number;
  opens: number;
  liked: boolean;
};
const Context = createContext<Record<string, Engagement>>({});
const changed = "devfeed:article-engagement";
function publish(value: Engagement) {
  window.dispatchEvent(new CustomEvent(changed, { detail: value }));
}
const count = (value: number) =>
  new Intl.NumberFormat("en", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);

export function EngagementProvider({
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
      const value = (event as CustomEvent<Engagement>).detail;
      writes.current[value.article_id] = Date.now();
      setValues((current) => ({ ...current, [value.article_id]: value }));
    };
    window.addEventListener(changed, update);
    return () => window.removeEventListener(changed, update);
  }, []);
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
              (writes.current[item.article_id] ?? 0) >= started &&
              current[item.article_id]
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

export function ArticleEngagement({
  articleId,
  trackOpen = false,
}: {
  articleId: string;
  trackOpen?: boolean;
}) {
  const values = useContext(Context);
  const value = values[articleId];
  const { user, loading } = useUser();
  const csrfToken = user?.csrf_token;
  const userId = user?.user_id;
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!trackOpen || loading) return;
    // Runs only when a preview/detail mounts in the browser, never on prefetch.
    // The server deduplicates retries, Strict Mode and reopening within an hour.
    userRequest<Engagement>(`articles/${articleId}/open`, {
      method: "POST",
      headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {},
    })
      .then(publish)
      .catch(() => {});
  }, [articleId, trackOpen, loading, userId, csrfToken]);
  async function toggle() {
    if (!user || busy) return;
    setBusy(true);
    setFailed(false);
    try {
      publish(
        await userRequest<Engagement>(`articles/${articleId}/like`, {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": user.csrf_token,
          },
          body: JSON.stringify({ liked: !value?.liked }),
        }),
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
      <Heart
        size={16}
        fill={user && value?.liked ? "currentColor" : "none"}
        aria-hidden="true"
      />
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
          href={`/api/v1/user/auth/login?return_to=${encodeURIComponent(`/articles/${articleId}`)}`}
          aria-label={`Sign in to like this article${likeCount}`}
          title="Sign in to like"
        >
          {heart}
        </a>
      )}
      {value && (
        <span
          className="open-count"
          title={`${value.opens} article opens`}
          aria-label={`${value.opens} article opens`}
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
