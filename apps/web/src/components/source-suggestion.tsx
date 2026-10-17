"use client";

import "../app/styles/settings.css";

import { readerLoginLink } from "@/lib/reader-runtime";
import Link from "@/components/reader-link";
import { useEffect, useRef, useState } from "react";
import { Check, LoaderCircle, Plus } from "lucide-react";
import { Select } from "@devfeed/ui/select";
import { AccountError } from "@/lib/user";
import { previewSourceSuggestion, suggestSource, type SourcePreview } from "@/lib/user-mutations";
import { AccountGate, useUser } from "./user-account";

function validFeedUrl(value: string) {
  if (/^@[\p{L}\p{N}_.\-·]{3,30}$/u.test(value)) return true;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password;
  } catch {
    return false;
  }
}

const returnTo = "/sources/suggest";
export function SuggestSourceLink() {
  const { user, loading } = useUser();
  if (loading) return null;
  return user ? (
    <Link href={returnTo} className="button">
      <Plus size={16} aria-hidden="true" />
      Suggest a source
    </Link>
  ) : (
    <a {...readerLoginLink(returnTo)} className="button">
      <Plus size={16} aria-hidden="true" />
      Suggest a source
    </a>
  );
}
export function SourceSuggestion() {
  return (
    <>
      <nav className="breadcrumbs" aria-label="Breadcrumb">
        <Link href="/sources">Sources</Link>
      </nav>
      <div className="page-heading">
        <h1>Suggest a source</h1>
      </div>
      <AccountGate
        returnTo={returnTo}
        title="Sign in to suggest a source"
        description="Share a developer-focused feed, YouTube channel, playlist, or video for review."
      >
        <SuggestionForm />
      </AccountGate>
    </>
  );
}
function SuggestionForm() {
  const { user, sessionRevision } = useUser();
  const account = useRef<AbortController | null>(null);
  const userId = user?.user_id;
  useEffect(() => {
    const controller = new AbortController();
    account.current = controller;
    return () => controller.abort();
  }, [userId, sessionRevision]);
  const [name, setName] = useState("");
  const [nameEdited, setNameEdited] = useState(false);
  const [preview, setPreview] = useState<(SourcePreview & { key: string; error?: string }) | null>(
    null,
  );
  const [url, setUrl] = useState("");
  const [youtubeTarget, setYoutubeTarget] = useState<"channel" | "playlist" | "video">();
  const [sourceType, setSourceType] = useState<"publisher" | "aggregator">("publisher");
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState<{ name: string } | null>(null);
  const previewKey = `${sourceType}:${youtubeTarget ?? ""}:${url.trim()}`;
  const canPreview = validFeedUrl(url.trim());
  const lookingUp = canPreview && preview?.key !== previewKey;
  const displayName = nameEdited ? name : preview?.key === previewKey ? preview.name : "";
  useEffect(() => {
    if (!user || !canPreview || saved) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const result = await previewSourceSuggestion(
          {
            feed_url: url.trim(),
            source_type: sourceType,
            ...(youtubeTarget && { youtube_target: youtubeTarget }),
          },
          {
            csrfToken: user.csrf_token,
            signal: AbortSignal.any([controller.signal, AbortSignal.timeout(45000)]),
          },
        );
        if (!controller.signal.aborted) setPreview({ ...result, key: previewKey });
      } catch (cause) {
        if (!controller.signal.aborted)
          setPreview({
            key: previewKey,
            name: "",
            error:
              cause instanceof AccountError && cause.status === 422
                ? "Couldn’t read this source. Check the public feed or YouTube URL."
                : cause instanceof AccountError && cause.status === 429
                  ? "Feed lookup limit reached. You can enter a name yourself."
                  : "Couldn’t look up the name. You can enter it yourself or retry when submitting.",
          });
      }
    }, 600);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [user, canPreview, url, sourceType, youtubeTarget, previewKey, saved]);
  if (saved)
    return (
      <section className="source-suggestion-confirmation" aria-labelledby="suggestion-confirmed">
        <div className="suggestion-confirmation-heading">
          <span className="suggestion-confirmation-icon">
            <Check size={20} aria-hidden="true" />
          </span>
          <h2
            id="suggestion-confirmed"
            tabIndex={-1}
            ref={(node) => {
              node?.focus();
            }}
          >
            Suggestion received
          </h2>
        </div>
        <p>Thanks for helping grow DevFeed. Your source will appear in Sources once approved.</p>
        <div className="suggestion-confirmation-source">
          <div>
            <strong>{saved.name}</strong>
            <span>{url.trim().startsWith("@") ? "YouTube" : new URL(url.trim()).hostname}</span>
          </div>
          <span className="suggestion-review-status">Pending review</span>
        </div>
        <div className="suggestion-confirmation-actions">
          <Link className="button primary" href="/sources">
            Back to sources
          </Link>
          <button
            className="button"
            onClick={() => {
              setSaved(null);
              setName("");
              setNameEdited(false);
              setPreview(null);
              setUrl("");
              setYoutubeTarget(undefined);
            }}
          >
            Suggest another source
          </button>
        </div>
      </section>
    );
  return (
    <section className="profile-panel" aria-label="Source suggestion">
      <p className="profile-description">
        Share a source about programming, developer tools, or computing. Suggestions are reviewed
        before appearing in the feed.
      </p>
      <form
        className="profile-form"
        onSubmit={async (event) => {
          event.preventDefault();
          if (!user || inFlight.current || !canPreview || lookingUp) return;
          const controller = account.current;
          if (!controller || controller.signal.aborted) return;
          inFlight.current = true;
          setBusy(true);
          setError("");
          try {
            const receipt = await suggestSource(
              {
                feed_url: url.trim(),
                source_type: sourceType,
                ...((youtubeTarget ||
                  (preview?.provider === "youtube" &&
                    preview.resource_kind !== "feed" &&
                    preview.resource_kind)) && {
                  youtube_target:
                    youtubeTarget ?? (preview!.resource_kind as "channel" | "playlist" | "video"),
                }),
                ...(displayName.trim() ? { name: displayName.trim() } : {}),
              },
              {
                csrfToken: user.csrf_token,
                signal: AbortSignal.any([controller.signal, AbortSignal.timeout(45000)]),
              },
            );
            if (!controller.signal.aborted) setSaved(receipt);
          } catch (cause) {
            if (!controller.signal.aborted)
              setError(
                cause instanceof AccountError && cause.status === 409
                  ? "This feed has already been added or suggested."
                  : cause instanceof AccountError && cause.status === 429
                    ? "You can suggest up to five sources per hour. Please try again later."
                    : cause instanceof AccountError && cause.status === 422
                      ? "Enter a public feed or YouTube URL that opens without signing in."
                      : cause instanceof AccountError && cause.status === 401
                        ? "Please sign in again to submit your suggestion."
                        : "Couldn’t submit your suggestion. Your details are still here; please try again.",
              );
          } finally {
            inFlight.current = false;
            setBusy(false);
          }
        }}
      >
        <fieldset disabled={busy}>
          <legend className="sr-only">Source details</legend>
          <div className="settings-field">
            <label htmlFor="suggest-feed-url">Feed or YouTube URL</label>
            <input
              id="suggest-feed-url"
              type="text"
              inputMode="url"
              required
              maxLength={2048}
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              placeholder="https://example.com/feed.xml"
              value={url}
              onChange={(event) => {
                setUrl(event.target.value);
                setYoutubeTarget(undefined);
                setError("");
              }}
              aria-describedby="suggest-url-help"
            />
            <p id="suggest-url-help">
              Use an RSS or Atom feed, YouTube @handle, channel, playlist, or video URL. A single
              video does not subscribe to channel uploads.
            </p>
          </div>
          {!!preview?.resources?.length && (
            <div className="settings-field">
              <label htmlFor="suggest-youtube-target">YouTube target</label>
              <Select
                id="suggest-youtube-target"
                label="YouTube target"
                value={
                  youtubeTarget ??
                  (preview.resource_kind === "feed" ? "video" : preview.resource_kind) ??
                  "video"
                }
                onChange={(value) => {
                  if (value === "video" || value === "playlist" || value === "channel")
                    setYoutubeTarget(value);
                }}
                options={preview.resources.map((item) => ({
                  value: item.target,
                  label:
                    item.target === "video"
                      ? "Only this video"
                      : item.target === "playlist"
                        ? "Entire playlist"
                        : "Channel uploads",
                }))}
              />
              <p>
                Choose what to submit. Videos are reviewed individually; a collection does not
                approve every upload.
              </p>
            </div>
          )}
          <div className="settings-field">
            <label htmlFor="suggest-source-name">Name (optional)</label>
            <input
              id="suggest-source-name"
              maxLength={200}
              value={displayName}
              onChange={(event) => {
                setNameEdited(true);
                setName(event.target.value);
              }}
              aria-describedby="suggest-name-help"
            />
            <p id="suggest-name-help" aria-live="polite">
              {lookingUp
                ? "Looking up the source name…"
                : preview?.key === previewKey && preview.error
                  ? preview.error
                  : "Filled from the source. You can edit it before submitting."}
            </p>
          </div>
          <div className="settings-field">
            <label htmlFor="suggest-source-type">Source type</label>
            <Select
              id="suggest-source-type"
              label="Source type"
              required
              disabled={busy}
              value={sourceType}
              onChange={(value) => {
                if (value === "publisher" || value === "aggregator") setSourceType(value);
              }}
              options={[
                { value: "publisher", label: "Publisher" },
                { value: "aggregator", label: "Aggregator" },
              ]}
            />
            <p>Publisher: original articles. Aggregator: links to articles from other sites.</p>
          </div>
        </fieldset>
        {error && (
          <p role="alert" className="profile-feedback error">
            {error}
          </p>
        )}
        <div className="profile-form-actions">
          <Link className="settings-button settings-button-ghost" href="/sources">
            Cancel
          </Link>
          <button
            className="settings-button"
            type="submit"
            disabled={busy || !canPreview || lookingUp}
            aria-busy={busy}
          >
            {busy && <LoaderCircle size={16} className="settings-spinner" aria-hidden="true" />}
            {busy ? "Checking source…" : "Submit suggestion"}
          </button>
        </div>
      </form>
    </section>
  );
}
