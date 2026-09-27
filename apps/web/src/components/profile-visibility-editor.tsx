"use client";
import type { ProfileVisibility } from "@/lib/user";
export function ProfileVisibilityEditor({
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
