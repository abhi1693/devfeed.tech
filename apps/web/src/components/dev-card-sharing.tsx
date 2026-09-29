"use client";
import { useId, useState, useSyncExternalStore } from "react";
import { CopyButton } from "@/components/copy-button";
import { Code2 } from "lucide-react";
import type { UserProfile } from "@/lib/user";
import { readerPublicOrigin } from "@/lib/reader-runtime";
import { trackEvent } from "@/lib/analytics";

const subscribe = () => () => {};
export function DevCardSharing({ profile, unsaved }: { profile: UserProfile; unsaved: boolean }) {
  const [message, setMessage] = useState("");
  const id = useId();
  const origin = useSyncExternalStore(subscribe, readerPublicOrigin, () => "");
  const available = Boolean(profile.username && profile.visibility?.public && !unsaved);
  const username = encodeURIComponent(profile.username ?? "");
  const path = `/users/${username}`;
  const url = origin ? new URL(path, origin).href : "";
  const imageUrl = origin ? new URL(`/api/v1/users/${username}/card.svg`, origin).href : "";
  const markdown = url ? `[![DevFeed card](${imageUrl})](${url})` : "";
  return (
    <section className="dev-card-sharing" aria-label="Share your dev card">
      {available ? (
        <>
          <div className="dev-card-embed">
            <label htmlFor={id}>
              <Code2 size={16} />
              Embed your card
            </label>
            <p>Add your live card to a GitHub README or any page that supports Markdown.</p>
            <textarea
              id={id}
              aria-label="Markdown embed code"
              readOnly
              rows={4}
              value={markdown}
              onFocus={(event) => event.currentTarget.select()}
            />
            <div className="dev-card-actions">
              <CopyButton
                key={markdown}
                text={markdown}
                label="Copy Markdown"
                disabled={!markdown}
                onStatusChange={(status) => {
                  if (status === "copied") {
                    trackEvent("dev_card_share", { method: "markdown" });
                    setMessage("Markdown copied.");
                  } else if (status === "failed") {
                    setMessage("Couldn’t copy. Select the embed code and copy it manually.");
                  } else setMessage("");
                }}
              >
                {() => "Copy Markdown"}
              </CopyButton>
            </div>
          </div>
          <p role="status">{message}</p>
        </>
      ) : (
        <p>
          {unsaved
            ? "Save your changes before sharing a link."
            : "To share a profile link or embed your card, claim a username and make your profile public, then save."}
        </p>
      )}
    </section>
  );
}
