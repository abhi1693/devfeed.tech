"use client";
import { trackEvent } from "@/lib/analytics";
import { LoadingReveal } from "./loading-reveal";
import { SaveFeedback } from "./motion-icon";
import { LoadingSkeleton } from "./loading-skeleton";

import { useEffect, useRef, useState } from "react";
import { ImageIcon, ImageOff, LoaderCircle, Plus, Save, Trash2 } from "lucide-react";
import { UserSettingsLayout } from "./user-settings-layout";
import { AccountGate, useUser } from "./user-account";
import {
  AccountError,
  type ProfileLink,
  type ProfileVisibility,
  type UserProfile,
  type UserStack,
} from "@/lib/user";
import { safeExternalUrl } from "@/lib/feed-query";
import { ProfileLinkIcon } from "./profile-link-icon";
import { InfiniteChoices } from "./infinite-choices";
import { CatalogIcon } from "./catalog-icon";
import { ProfileAvatar } from "./profile-avatar";
import { readDevCardDraft, clearDevCardDraft } from "@/lib/dev-card-draft";
import { DevCardPreview } from "./dev-card-preview";
import { Select } from "@devfeed/ui/select";
import type { Topic } from "@/lib/types";

const emptyVisibility: ProfileVisibility = {
  public: true,
  location: true,
  stack: true,
  heatmap: true,
  achievements: false,
};

function profileDefaults(initial: UserProfile, providerName: string | null) {
  return {
    ...initial,
    display_name: initial.display_name ?? providerName ?? "",
    username: initial.username ?? null,
    bio: initial.bio ?? null,
    location: initial.location ?? null,
    about: initial.about ?? null,
    links: initial.links ?? [],
    stack: initial.stack ?? [],
    visibility: {
      ...emptyVisibility,
      ...initial.visibility,
      location: true,
      stack: true,
      heatmap: true,
    },
  } satisfies UserProfile;
}

export function ProfileSettings() {
  return (
    <AccountGate returnTo="/settings/profile">
      <ProfileContent />
    </AccountGate>
  );
}

// Matches admin SettingsNav, horizontal Field and SettingsForm presentation.
// Only sections and identity fields available to public accounts are shown.
function ProfileContent() {
  const { user, profile, profileUnavailable, refreshProfile } = useUser();
  return (
    <UserSettingsLayout section="profile">
      <section className="profile-panel" aria-label="Profile">
        <LoadingReveal
          loading={!profile && !profileUnavailable}
          fallback={<LoadingSkeleton kind="form" label="Loading your profile…" />}
        >
          {profileUnavailable ? (
            <div className="profile-load-error">
              <h2>Couldn’t load your profile</h2>
              <p>Please try again.</p>
              <button className="settings-button" onClick={refreshProfile}>
                Retry
              </button>
            </div>
          ) : profile ? (
            <ProfileForm key={user!.user_id} initial={profile} />
          ) : null}
        </LoadingReveal>
      </section>
    </UserSettingsLayout>
  );
}

function AvatarUrlPreview({ value }: { value: string | null }) {
  const url = safeExternalUrl(value) ?? null;
  const [settledUrl, setSettledUrl] = useState(url);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettledUrl(url), 300);
    return () => window.clearTimeout(timer);
  }, [url]);
  return (
    <AvatarPreview key={settledUrl === url ? url : ""} src={settledUrl === url ? url : null} />
  );
}

function AvatarPreview({ src }: { src: string | null }) {
  const [status, setStatus] = useState<"loading" | "loaded" | "failed">("loading");
  const Icon = !src ? ImageIcon : status === "failed" ? ImageOff : LoaderCircle;
  const label = !src
    ? "No avatar preview"
    : status === "failed"
      ? "Preview unavailable"
      : "Loading preview";
  return (
    <span className="settings-avatar-preview">
      {(!src || status !== "loaded") && (
        <span role="status" aria-label={label} title={label}>
          <Icon
            size={16}
            aria-hidden="true"
            className={src && status === "loading" ? "settings-spinner" : undefined}
          />
        </span>
      )}
      {src && status !== "failed" && (
        // External images load directly in the browser, as in admin LogoUrlField.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={src}
          alt="Avatar preview"
          referrerPolicy="no-referrer"
          decoding="async"
          ref={(image) => {
            if (image?.complete) setStatus(image.naturalWidth > 0 ? "loaded" : "failed");
          }}
          onLoad={() => setStatus("loaded")}
          onError={() => setStatus("failed")}
          style={{ visibility: status === "loaded" ? "visible" : "hidden" }}
        />
      )}
    </span>
  );
}

function ProfileForm({ initial }: { initial: UserProfile }) {
  const { user, saveProfile } = useUser();
  const [baseline, setBaseline] = useState(() => profileDefaults(initial, user?.name ?? null));
  const [draft, setDraft] = useState(() => {
    const candidate = readDevCardDraft();
    return candidate?.ready ? candidate : null;
  });
  const [value, setValue] = useState(() =>
    draft
      ? {
          ...baseline,
          display_name: draft.name || baseline.display_name,
          stack: draft.stack.length ? draft.stack : baseline.stack,
        }
      : baseline,
  );
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState(false);
  const dirty = JSON.stringify(value) !== JSON.stringify(baseline);
  useEffect(() => {
    if (draft && !dirty) clearDevCardDraft();
  }, [draft, dirty]);
  function change(next: UserProfile) {
    setValue(profileDefaults(next, user?.name ?? null));
    setMessage("");
    setError(false);
  }
  return (
    <div className="profile-editor-layout">
      <form
        className="profile-form profile-direct"
        onSubmit={async (event) => {
          event.preventDefault();
          if (busy || !dirty) return;
          setBusy(true);
          setMessage("");
          setError(false);
          try {
            const saved = profileDefaults(await saveProfile(value), user?.name ?? null);
            setValue(saved);
            setBaseline(saved);
            if (draft) trackEvent("dev_card_saved", {});
            clearDevCardDraft();
            setDraft(null);
            setMessage("Your profile is saved.");
          } catch (cause) {
            setError(true);
            setMessage(
              cause instanceof AccountError && cause.status === 409
                ? "That username is unavailable or already claimed."
                : cause instanceof AccountError && cause.status === 422
                  ? "Check your fields and use public HTTP or HTTPS links."
                  : "Couldn’t save your profile. Your changes are still here; please try again.",
            );
          } finally {
            setBusy(false);
          }
        }}
      >
        <fieldset disabled={busy}>
          <legend className="sr-only">Profile details</legend>
          {draft && dirty && (
            <p role="status">
              Your dev card preview is ready. Review your details and save changes to keep it.
            </p>
          )}
          <div className="profile-direct-heading">
            <ProfileAvatar name={value.display_name} url={value.avatar_url} />
            <div>
              <h2>Profile</h2>
            </div>
          </div>
          <div className="profile-field-grid">
            <div className="direct-field">
              <label htmlFor="profile-name">Display name</label>
              <input
                id="profile-name"
                maxLength={100}
                autoComplete="nickname"
                value={value.display_name ?? ""}
                onChange={(event) => change({ ...value, display_name: event.target.value })}
              />
            </div>
            <div className="direct-field">
              <label htmlFor="profile-username">Username</label>
              <input
                id="profile-username"
                maxLength={30}
                readOnly={Boolean(baseline.username)}
                value={value.username ?? ""}
                onChange={(event) => change({ ...value, username: event.target.value || null })}
                aria-describedby={baseline.username ? undefined : "profile-username-help"}
              />
              {!baseline.username && (
                <p id="profile-username-help">
                  Permanent once saved. 3–30 letters, numbers, _ or -.
                </p>
              )}
            </div>
            <div className="direct-field direct-field-wide">
              <label htmlFor="profile-bio">Short bio</label>
              <input
                id="profile-bio"
                maxLength={160}
                placeholder="What you build, use, or enjoy learning"
                value={value.bio ?? ""}
                onChange={(event) => change({ ...value, bio: event.target.value || null })}
              />
            </div>
            <div className="direct-field">
              <label htmlFor="profile-avatar">Avatar URL</label>
              <div className="settings-avatar-input">
                <input
                  id="profile-avatar"
                  type="url"
                  maxLength={2048}
                  placeholder="https://…"
                  value={value.avatar_url ?? ""}
                  onChange={(event) => change({ ...value, avatar_url: event.target.value })}
                />
                <AvatarUrlPreview value={value.avatar_url} />
              </div>
            </div>
            <div className="direct-field">
              <label htmlFor="profile-location">Location</label>
              <input
                id="profile-location"
                maxLength={100}
                autoComplete="address-level2"
                placeholder="City or region"
                value={value.location ?? ""}
                onChange={(event) => change({ ...value, location: event.target.value || null })}
              />
            </div>
            <div className="direct-field direct-field-wide">
              <label htmlFor="profile-about">About</label>
              <textarea
                id="profile-about"
                rows={2}
                maxLength={5000}
                placeholder="More about your work and interests, if you’d like."
                value={value.about ?? ""}
                onChange={(event) => change({ ...value, about: event.target.value || null })}
              />
            </div>
          </div>
          <LinksEditor links={value.links} onChange={(links) => change({ ...value, links })} />
          <StackEditor stack={value.stack} onChange={(stack) => change({ ...value, stack })} />
          <VisibilityEditor
            visibility={value.visibility}
            onChange={(visibility) => change({ ...value, visibility })}
          />
        </fieldset>
        <div className="profile-direct-footer">
          <p
            role={error ? "alert" : "status"}
            className={error ? "profile-feedback error" : "profile-feedback"}
          >
            {message || (dirty ? "Unsaved changes" : "")}
          </p>
          <div className="profile-form-actions">
            <button
              type="button"
              className="settings-button settings-button-ghost"
              disabled={busy || !dirty}
              onClick={() => {
                setValue(baseline);
                setMessage("");
                setError(false);
              }}
            >
              Discard changes
            </button>
            <button
              type="submit"
              className="settings-button"
              disabled={busy || !dirty}
              aria-busy={busy}
            >
              <SaveFeedback
                busy={busy}
                saved={!!message && !error && !dirty}
                idle={<Save size={16} />}
              />
              {busy ? "Saving…" : "Save changes"}
            </button>
          </div>
        </div>
      </form>
      {user && (
        <DevCardPreview
          profile={{ ...value, reading_streak: initial.reading_streak }}
          user={user}
          unsaved={dirty}
        />
      )}
    </div>
  );
}
function LinksEditor({
  links,
  onChange,
}: {
  links: ProfileLink[];
  onChange: (links: ProfileLink[]) => void;
}) {
  const rows = links.length ? links : [{ url: "", label: null }];
  return (
    <section className="profile-direct-section" aria-label="Links">
      <div className="profile-direct-section-heading">
        <h3>Links</h3>
        <button
          type="button"
          className="settings-button settings-button-ghost"
          disabled={links.length >= 20 || !rows.at(-1)?.url.trim()}
          onClick={() => onChange([...links, { url: "", label: null }])}
        >
          <Plus size={14} aria-hidden="true" />
          Add link
        </button>
      </div>
      <div className="profile-direct-links">
        {rows.map((link, index) => (
          <div className="profile-direct-link" key={index}>
            <div className="profile-direct-link-url">
              <input
                aria-label={`Link ${index + 1} URL`}
                type="url"
                maxLength={2048}
                placeholder="https://github.com/you"
                value={link.url}
                onChange={(event) =>
                  onChange(
                    rows.map((item, i) =>
                      i === index ? { url: event.target.value, label: null } : item,
                    ),
                  )
                }
              />
              <ProfileLinkIcon url={link.url} />
            </div>
            <button
              type="button"
              className="settings-icon-button"
              aria-label={`Remove link ${index + 1}`}
              disabled={!links.length}
              onClick={() => onChange(links.filter((_, i) => i !== index))}
            >
              <Trash2 size={15} aria-hidden="true" />
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}

function StackEditor({
  stack,
  onChange,
}: {
  stack: UserStack[];
  onChange: (stack: UserStack[]) => void;
}) {
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const resultsRef = useRef<HTMLDivElement>(null);
  const sections = {
    primary: "Use regularly",
    hobby: "Side projects",
    learning: "Learning",
    past: "Used before",
  } as const;
  return (
    <section className="profile-direct-section" aria-label="Developer stack">
      <div className="profile-direct-section-heading">
        <h3>Developer stack</h3>
        <span>Usage and year are optional</span>
      </div>
      <input
        ref={searchRef}
        type="search"
        aria-label="Find a language, framework, or tool"
        placeholder="Type to add a language, framework, or tool…"
        value={query}
        maxLength={200}
        disabled={stack.length >= 100}
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing) return;
          if (event.key === "Escape") {
            event.preventDefault();
            setQuery("");
          }
          if (event.key === "ArrowDown") {
            event.preventDefault();
            resultsRef.current?.querySelector<HTMLButtonElement>("button[data-add-topic]")?.focus();
          }
          if (event.key === "Enter") {
            event.preventDefault();
            resultsRef.current?.querySelector<HTMLButtonElement>("button[data-add-topic]")?.click();
          }
        }}
      />
      {query.trim() && (
        <div ref={resultsRef} className="profile-direct-results" aria-label="Matching stack topics">
          <InfiniteChoices<Topic> label="topics" query={query}>
            {(topics, complete) => {
              const available = topics.filter(
                (topic) => !stack.some((entry) => entry.topic_id === topic.id),
              );
              return (
                <>
                  {available.map((topic) => (
                    <button
                      data-add-topic
                      key={topic.id}
                      type="button"
                      aria-label={`Add ${topic.name}`}
                      onClick={() => {
                        onChange([
                          ...stack,
                          {
                            topic_id: topic.id,
                            name: topic.name,
                            slug: topic.slug,
                            kind: topic.kind,
                            logo_url: topic.logo_url,
                            status: "active",
                            section: "primary",
                            since_year: null,
                          },
                        ]);
                        setQuery("");
                        searchRef.current?.focus();
                      }}
                    >
                      <CatalogIcon url={topic.logo_url} />
                      <span>{topic.name}</span>
                      <small>{topic.kind.replaceAll("_", " ")}</small>
                      <Plus size={14} aria-hidden="true" />
                    </button>
                  ))}
                  {complete && topics.length > 0 && !available.length && (
                    <p>Matching technologies are already in your profile.</p>
                  )}
                </>
              );
            }}
          </InfiniteChoices>
        </div>
      )}
      {stack.length > 0 && (
        <div className="profile-direct-technologies">
          {stack.map((item) => (
            <div className="profile-direct-technology" key={item.topic_id}>
              <span className="profile-direct-technology-name">
                <CatalogIcon url={item.logo_url} />
                <span>
                  {item.name}
                  {item.status && item.status !== "active" && <small>No longer listed</small>}
                </span>
              </span>
              <Select
                label={`Usage for ${item.name}`}
                required
                value={item.section || "primary"}
                options={Object.entries(sections).map(([value, label]) => ({ value, label }))}
                onChange={(section) =>
                  onChange(
                    stack.map((entry) =>
                      entry.topic_id === item.topic_id
                        ? { ...entry, section: section as UserStack["section"] }
                        : entry,
                    ),
                  )
                }
              />
              <input
                aria-label={`Since year for ${item.name}`}
                type="number"
                min={1900}
                max={new Date().getUTCFullYear()}
                placeholder="Since year"
                value={item.since_year ?? ""}
                onChange={(event) =>
                  onChange(
                    stack.map((entry) =>
                      entry.topic_id === item.topic_id
                        ? {
                            ...entry,
                            since_year: event.target.value ? Number(event.target.value) : null,
                          }
                        : entry,
                    ),
                  )
                }
              />
              <button
                type="button"
                className="settings-icon-button"
                aria-label={`Remove ${item.name}`}
                onClick={() => onChange(stack.filter((entry) => entry.topic_id !== item.topic_id))}
              >
                <Trash2 size={15} aria-hidden="true" />
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function VisibilityEditor({
  visibility,
  onChange,
}: {
  visibility: ProfileVisibility;
  onChange: (visibility: ProfileVisibility) => void;
}) {
  return (
    <section className="profile-direct-section profile-direct-visibility" aria-label="Visibility">
      <h3>Visibility</h3>
      <label>
        <input
          type="checkbox"
          checked={visibility.public}
          onChange={(event) => onChange({ ...visibility, public: event.target.checked })}
        />
        Make my profile public
      </label>
    </section>
  );
}
