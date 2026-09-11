"use client";
import { useEffect, useState } from "react";
import { useUser } from "./user-account";
import { AccountError, userRequest, type Preferences } from "@/lib/user";

export function TopicFollow({
  topicId,
  articleId,
}: {
  topicId: string;
  articleId: string;
}) {
  const { user, loading } = useUser();
  const [state, setState] = useState<{
    owner: string;
    followed: boolean;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!user) return;
    const controller = new AbortController();
    userRequest<Preferences>("preferences", {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
    })
      .then((value) =>
        setState({
          owner: user.user_id,
          followed: value.topic_ids.includes(topicId),
        }),
      )
      .catch(() => {
        if (!controller.signal.aborted)
          setError("Couldn’t load your topics. Reload to try again.");
      });
    return () => controller.abort();
  }, [user, topicId]);
  async function toggle() {
    if (!user || state?.owner !== user.user_id) return;
    setBusy(true);
    setError("");
    try {
      const result = await userRequest<{ followed: boolean }>(
        `preferences/topics/${topicId}`,
        {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": user.csrf_token,
          },
          body: JSON.stringify({ followed: !state.followed }),
        },
      );
      setState({ owner: user.user_id, followed: result.followed });
    } catch (error) {
      setError(
        error instanceof AccountError && error.status === 422
          ? "You can follow up to 100 active topics. Manage your topics to make room."
          : "Couldn’t update this topic. Please try again.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="topic-follow">
      {!loading && !user ? (
        <a
          className="button"
          href={`/api/v1/user/auth/login?return_to=${encodeURIComponent(`/articles/${articleId}`)}`}
        >
          Follow
        </a>
      ) : (
        <button
          className="button"
          type="button"
          aria-pressed={
            state?.owner === user?.user_id && state?.followed === true
          }
          disabled={loading || busy || state?.owner !== user?.user_id}
          onClick={toggle}
        >
          {busy
            ? "Saving…"
            : state?.owner === user?.user_id && state?.followed
              ? "Following"
              : "Follow"}
        </button>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
