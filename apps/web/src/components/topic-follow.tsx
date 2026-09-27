"use client";
import { useUser } from "./user-account";
import { useTopicFollows } from "./topic-follows";
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
  const { user } = useUser();
  const { ids, loading, unavailable, busy, errors, messages, toggle, refresh } = useTopicFollows();
  const pending = busy.includes(topicId) || busy.includes("all");
  const error = errors[topicId];
  return (
    <div className="topic-follow">
      <FollowButton
        signedIn={!!user}
        loading={loading}
        followed={ids.includes(topicId)}
        pending={pending}
        disabled={loading || pending || unavailable}
        returnTo={returnTo ?? (articleSlug ? `/articles/${articleSlug}` : `/topics/${topicId}`)}
        onClick={() => void toggle(topicId)}
      />
      {unavailable && (
        <p role="alert">
          Couldn’t load your topics.{" "}
          <button className="settings-button" onClick={refresh}>
            Retry topic preferences
          </button>
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      <span className="sr-only" role="status">
        {messages[topicId]}
      </span>
    </div>
  );
}
