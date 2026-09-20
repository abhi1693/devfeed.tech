"use client";
import { useEffect, useState } from "react";
import { useUser } from "./user-account";
import { AccountError, userRequest, type Preferences } from "@/lib/user";
import { FollowButton } from "./follow-button";

export function TopicFollow({
  topicId,
  articleSlug,
  returnTo,
}: {
  topicId: string;
  articleSlug?: string;
  returnTo?: string;
}) {
  const { user, loading } = useUser();
  const [state, setState] = useState<{
    owner: string;
    followed: boolean;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
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
        if (!controller.signal.aborted) setError("Couldn’t load your topics. Reload to try again.");
      });
    return () => controller.abort();
  }, [user, topicId]);
  async function toggle() {
    if (!user || busy || state?.owner !== user.user_id) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await userRequest<{ followed: boolean }>(`preferences/topics/${topicId}`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": user.csrf_token,
        },
        body: JSON.stringify({ followed: !state.followed }),
      });
      setState({ owner: user.user_id, followed: result.followed });
      setMessage(result.followed ? "Topic followed." : "Topic unfollowed.");
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
      <FollowButton
        signedIn={!!user}
        loading={loading}
        followed={state?.owner === user?.user_id && state?.followed === true}
        pending={busy}
        disabled={loading || busy || state?.owner !== user?.user_id}
        returnTo={returnTo ?? (articleSlug ? `/articles/${articleSlug}` : `/topics/${topicId}`)}
        onClick={toggle}
      />
      {error && <p role="alert">{error}</p>}
      <span className="sr-only" role="status">
        {message}
      </span>
    </div>
  );
}
