"use client";
import type { ProfileEditorValue } from "@/lib/use-profile-editor";
import { ProfileAvatar } from "./profile-avatar";
import { AvatarUrlPreview } from "./avatar-url-preview";
import { AvatarUpload } from "./avatar-upload";
import type { UserProfile } from "@/lib/user";
export function ProfileIdentityFields({
  value,
  usernameClaimed,
  onChange: change,
  onAvatarSaved,
  onAvatarBusy,
}: {
  value: ProfileEditorValue;
  usernameClaimed: boolean;
  onChange: (value: ProfileEditorValue) => void;
  onAvatarSaved: (value: UserProfile) => void;
  onAvatarBusy: (busy: boolean) => void;
}) {
  return (
    <>
      <div className="profile-direct-heading">
        <ProfileAvatar
          name={value.display_name}
          url={value.avatar_url}
          variants={value.avatar_variants}
          size={40}
        />
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
            readOnly={usernameClaimed}
            value={value.username ?? ""}
            onChange={(event) => change({ ...value, username: event.target.value || null })}
            aria-describedby={usernameClaimed ? undefined : "profile-username-help"}
          />
          {!usernameClaimed && (
            <p id="profile-username-help">Permanent once saved. 3–30 letters, numbers, _ or -.</p>
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
              onChange={(event) =>
                change({ ...value, avatar_url: event.target.value, avatar_variants: [] })
              }
            />
            <AvatarUrlPreview value={value.avatar_url} variants={value.avatar_variants} />
          </div>
          <AvatarUpload
            hasAvatar={Boolean(value.avatar_url)}
            onSaved={onAvatarSaved}
            onBusy={onAvatarBusy}
          />
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
    </>
  );
}
