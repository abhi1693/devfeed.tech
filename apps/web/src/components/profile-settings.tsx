"use client";
import { useState } from "react";
import { Save } from "lucide-react";
import type { UserProfile } from "@/lib/user";
import { useProfileEditor } from "@/lib/use-profile-editor";
import { AccountGate, useUser } from "./user-account";
import { UserSettingsLayout } from "./user-settings-layout";
import { LoadingReveal } from "./loading-reveal";
import { LoadingSkeleton } from "./loading-skeleton";
import { SaveFeedback } from "./motion-icon";
import { DevCardPreview, type DevCardPreviewFormat } from "./dev-card-preview";
import { ProfileIdentityFields } from "./profile-identity-fields";
import { ProfileLinksEditor } from "./profile-links-editor";
import { ProfileStackEditor } from "./profile-stack-editor";
import { ProfileCardFields } from "./profile-card-fields";
import { ProfileVisibilityEditor } from "./profile-visibility-editor";

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

function ProfileForm({ initial }: { initial: UserProfile }) {
  const { user, saveProfile } = useUser();
  const [previewFormat, setPreviewFormat] = useState<DevCardPreviewFormat>("card");
  const [avatarBusy, setAvatarBusy] = useState(false);
  const {
    value,
    baseline,
    draft,
    busy,
    message,
    error,
    dirty,
    change,
    changeStack,
    save,
    discard,
    acceptAvatar,
  } = useProfileEditor(initial, user?.name ?? null, saveProfile);
  return (
    <div className="profile-editor-layout">
      <form
        className="profile-form profile-direct"
        onSubmit={(event) => {
          event.preventDefault();
          if (!avatarBusy) void save();
        }}
      >
        <fieldset disabled={busy || avatarBusy}>
          <legend className="sr-only">Profile details</legend>
          {draft && dirty && (
            <p role="status">
              Your dev card preview is ready. Review your details and save changes to keep it.
            </p>
          )}
          <ProfileIdentityFields
            value={value}
            usernameClaimed={Boolean(baseline.username)}
            onChange={change}
            onAvatarSaved={acceptAvatar}
            onAvatarBusy={setAvatarBusy}
          />
          <ProfileLinksEditor
            links={value.links}
            onChange={(links) => change({ ...value, links })}
          />
          <ProfileStackEditor stack={value.stack} onChange={changeStack} />
          <ProfileCardFields value={value} previewFormat={previewFormat} onChange={change} />
          <ProfileVisibilityEditor
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
              disabled={busy || avatarBusy || !dirty}
              onClick={discard}
            >
              Discard changes
            </button>
            <button
              type="submit"
              className="settings-button"
              disabled={busy || avatarBusy || !dirty}
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
          format={previewFormat}
          onFormatChange={setPreviewFormat}
          profile={{ ...value, reading_streak: initial.reading_streak }}
          user={user}
          unsaved={dirty}
        />
      )}
    </div>
  );
}
