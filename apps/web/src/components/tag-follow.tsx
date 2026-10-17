"use client";
import { useUser } from "./user-account";
import { useTagFollows } from "./tag-follows";
import { FollowButton } from "./follow-button";

export function TagFollow({ tagId, returnTo }: { tagId: string; returnTo?: string }) {
  const { user } = useUser();
  const { ids, loading, unavailable, busy, errors, messages, toggle, refresh } = useTagFollows();
  const pending = busy.includes(tagId) || busy.includes("all");
  const error = errors[tagId];
  return (
    <div className="tag-follow">
      <FollowButton
        signedIn={!!user}
        loading={loading}
        followed={ids.includes(tagId)}
        pending={pending}
        disabled={loading || pending || unavailable}
        returnTo={returnTo ?? "/tags"}
        onClick={() => void toggle(tagId)}
      />
      {unavailable && (
        <p role="alert">
          Couldn’t load your tags.{" "}
          <button className="settings-button" onClick={refresh}>
            Retry tag preferences
          </button>
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      <span className="sr-only" role="status">
        {messages[tagId]}
      </span>
    </div>
  );
}
