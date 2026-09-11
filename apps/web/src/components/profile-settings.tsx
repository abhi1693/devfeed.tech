"use client";

import { useEffect, useState } from "react";
import {
  ImageIcon,
  ImageOff,
  LoaderCircle,
} from "lucide-react";
import { UserSettingsLayout } from "./user-settings-layout";
import { AccountGate, useUser } from "./user-account";
import { AccountError, type UserProfile } from "@/lib/user";
import { safeExternalUrl } from "@/lib/feed-query";

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
        <p className="profile-description">
          Personalize how your account appears in DevFeed.
        </p>
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
        ) : (
          <p role="status">Loading your profile…</p>
        )}
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
    <AvatarPreview
      key={settledUrl === url ? url : ""}
      src={settledUrl === url ? url : null}
    />
  );
}

function AvatarPreview({ src }: { src: string | null }) {
  const [status, setStatus] = useState<"loading" | "loaded" | "failed">(
    "loading",
  );
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
            className={
              src && status === "loading" ? "settings-spinner" : undefined
            }
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
            if (image?.complete)
              setStatus(image.naturalWidth > 0 ? "loaded" : "failed");
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
  const [value, setValue] = useState(initial);
  const [baseline, setBaseline] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState(false);
  const dirty = JSON.stringify(value) !== JSON.stringify(baseline);
  function change(next: UserProfile) {
    setValue(next);
    setMessage("");
    setError(false);
  }
  return (
    <form
      className="profile-form"
      onSubmit={async (event) => {
        event.preventDefault();
        if (busy || !dirty) return;
        setBusy(true);
        setMessage("");
        setError(false);
        try {
          const saved = await saveProfile(value);
          setValue(saved);
          setBaseline(saved);
          setMessage("Your profile is saved.");
        } catch (cause) {
          setError(true);
          setMessage(
            cause instanceof AccountError && cause.status === 422
              ? "Check your display name and use a public HTTP or HTTPS image URL."
              : "Couldn’t save your profile. Your changes are still here; please try again.",
          );
        } finally {
          setBusy(false);
        }
      }}
    >
      <fieldset disabled={busy}>
        <legend className="sr-only">Profile details</legend>
        <div className="settings-field">
          <label htmlFor="profile-name">Display name</label>
          <input
            id="profile-name"
            maxLength={100}
            autoComplete="nickname"
            placeholder={user!.name || "Your name"}
            value={value.display_name ?? ""}
            onChange={(event) =>
              change({ ...value, display_name: event.target.value || null })
            }
            aria-describedby="profile-name-help"
          />
          <p id="profile-name-help">
            Used in your account menu. Your sign-in identity stays managed by
            your account provider.
          </p>
        </div>
        <div className="settings-field">
          <label htmlFor="profile-avatar">Avatar URL</label>
          <div className="settings-avatar-input">
            <input
              id="profile-avatar"
              type="url"
              inputMode="url"
              spellCheck={false}
              autoCapitalize="none"
              autoComplete="off"
              maxLength={2048}
              value={value.avatar_url ?? ""}
              onChange={(event) =>
                change({ ...value, avatar_url: event.target.value || null })
              }
              aria-describedby="profile-avatar-help"
            />
            <AvatarUrlPreview value={value.avatar_url} />
          </div>
          <p id="profile-avatar-help">Use a public image URL.</p>
        </div>
        <section className="profile-identity" aria-labelledby="profile-sign-in">
          <h2 id="profile-sign-in">Managed by your account provider</h2>
          <dl>
            <div>
              <dt>Name</dt>
              <dd>{user!.name || "Not provided"}</dd>
            </div>
            <div>
              <dt>Email</dt>
              <dd>{user!.email || "Not provided"}</dd>
            </div>
          </dl>
        </section>
      </fieldset>
      {message && (
        <p
          className={`profile-feedback${error ? " error" : ""}`}
          role={error ? "alert" : "status"}
        >
          {message}
        </p>
      )}
      <div className="profile-form-actions">
        <button
          className="settings-button settings-button-ghost"
          type="button"
          disabled={busy}
          onClick={() => change({ display_name: null, avatar_url: null })}
        >
          Reset to defaults
        </button>
        <button
          className="settings-button"
          type="submit"
          disabled={busy || !dirty}
          aria-busy={busy}
        >
          {busy && (
            <LoaderCircle
              size={16}
              className="settings-spinner"
              aria-hidden="true"
            />
          )}
          {busy ? "Saving…" : "Save changes"}
        </button>
      </div>
      {dirty && (
        <p className="profile-unsaved" role="status">
          You have unsaved changes.
        </p>
      )}
    </form>
  );
}
