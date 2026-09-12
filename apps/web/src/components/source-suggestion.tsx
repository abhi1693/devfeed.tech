"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Check, LoaderCircle, Plus } from "lucide-react";
import { Select } from "@devfeed/ui/select";
import { AccountError, userRequest } from "@/lib/user";
import { AccountGate, useUser } from "./user-account";

function validFeedUrl(value: string) {
  try {
    return ["http:", "https:"].includes(new URL(value).protocol);
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
    <a
      href={`/api/v1/user/auth/login?return_to=${encodeURIComponent(returnTo)}`}
      className="button"
    >
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
        description="Share a developer-focused RSS or Atom feed for review."
      >
        <SuggestionForm />
      </AccountGate>
    </>
  );
}
function SuggestionForm() {
  const { user } = useUser();
  const [name, setName] = useState("");
  const [nameEdited, setNameEdited] = useState(false);
  const [preview, setPreview] = useState<{ key: string; name: string; error?: string } | null>(
    null,
  );
  const [url, setUrl] = useState("");
  const [sourceType, setSourceType] = useState("publisher");
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState<{ name: string } | null>(null);
  const previewKey = `${sourceType}:${url.trim()}`;
  const canPreview = validFeedUrl(url.trim());
  const lookingUp = canPreview && preview?.key !== previewKey;
  const displayName = nameEdited ? name : preview?.key === previewKey ? preview.name : "";
  useEffect(() => {
    if (!user || !canPreview || saved) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const result = await userRequest<{ name: string }>("sources/suggestions/preview", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": user.csrf_token },
          body: JSON.stringify({ feed_url: url.trim(), source_type: sourceType }),
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(45000)]),
        });
        if (!controller.signal.aborted) setPreview({ key: previewKey, name: result.name });
      } catch (cause) {
        if (!controller.signal.aborted)
          setPreview({
            key: previewKey,
            name: "",
            error:
              cause instanceof AccountError && cause.status === 422
                ? "Couldn’t read this feed. Check the RSS or Atom URL."
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
  }, [user, canPreview, url, sourceType, previewKey, saved]);
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
            <span>{new URL(url.trim()).hostname}</span>
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
          if (!user || inFlight.current || lookingUp) return;
          inFlight.current = true;
          setBusy(true);
          setError("");
          try {
            const receipt = await userRequest<{ name: string }>("sources/suggestions", {
              method: "POST",
              headers: { "Content-Type": "application/json", "X-CSRF-Token": user.csrf_token },
              body: JSON.stringify({
                feed_url: url.trim(),
                source_type: sourceType,
                ...(displayName.trim() ? { name: displayName.trim() } : {}),
              }),
              signal: AbortSignal.timeout(45000),
            });
            setSaved(receipt);
          } catch (cause) {
            setError(
              cause instanceof AccountError && cause.status === 409
                ? "This feed has already been added or suggested."
                : cause instanceof AccountError && cause.status === 429
                  ? "You can suggest up to five sources per hour. Please try again later."
                  : cause instanceof AccountError && cause.status === 422
                    ? "Check the URL: it must be a reachable public RSS or Atom feed, without credentials or private addresses."
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
            <label htmlFor="suggest-feed-url">RSS or Atom URL</label>
            <input
              id="suggest-feed-url"
              type="url"
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
                setError("");
              }}
              aria-describedby="suggest-url-help"
            />
            <p id="suggest-url-help">Use the feed URL, not a website or article link.</p>
          </div>
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
                ? "Looking up the feed name…"
                : preview?.key === previewKey && preview.error
                  ? preview.error
                  : "Filled from the feed. You can edit it before submitting."}
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
              onChange={setSourceType}
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
            disabled={busy || !url.trim() || lookingUp}
            aria-busy={busy}
          >
            {busy && <LoaderCircle size={16} className="settings-spinner" aria-hidden="true" />}
            {busy ? "Checking feed…" : "Submit suggestion"}
          </button>
        </div>
      </form>
    </section>
  );
}
