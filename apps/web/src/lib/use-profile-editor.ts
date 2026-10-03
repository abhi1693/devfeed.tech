"use client";
import { useEffect, useMemo, useState } from "react";
import { trackEvent } from "./analytics";
import { AccountError, type ProfileVisibility, type UserProfile, type UserStack } from "./user";
import { readDevCardDraft, clearDevCardDraft } from "./dev-card-draft";
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

export type ProfileEditorValue = ReturnType<typeof profileDefaults>;

/** Mount once per account; incoming profile refreshes must not replace unsaved edits. */
export function useProfileEditor(
  initial: UserProfile,
  providerName: string | null,
  saveProfile: (value: UserProfile) => Promise<UserProfile>,
) {
  const [baseline, setBaseline] = useState(() => profileDefaults(initial, providerName));
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
  const dirty = useMemo(
    () => JSON.stringify(value) !== JSON.stringify(baseline),
    [value, baseline],
  );
  useEffect(() => {
    if (draft && !dirty) clearDevCardDraft();
  }, [draft, dirty]);
  function change(next: UserProfile) {
    setValue(profileDefaults(next, providerName));
    setMessage("");
    setError(false);
  }

  async function save() {
    if (busy || !dirty) return;
    setBusy(true);
    setMessage("");
    setError(false);
    try {
      const saved = profileDefaults(await saveProfile(value), providerName);
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
  }
  function discard() {
    setValue(baseline);
    setMessage("");
    setError(false);
  }
  function acceptAvatar(saved: UserProfile) {
    const avatar = { avatar_url: saved.avatar_url, avatar_variants: saved.avatar_variants ?? [] };
    setValue((current) => ({ ...current, ...avatar }));
    setBaseline((current) => ({ ...current, ...avatar }));
    setMessage(saved.avatar_url ? "Avatar updated." : "Avatar removed.");
    setError(false);
  }
  function changeStack(stack: UserStack[]) {
    change({
      ...value,
      stack,
      dev_card: value.dev_card?.technologies
        ? {
            ...value.dev_card,
            technologies: value.dev_card.technologies.filter((id) =>
              stack.some((item) => item.topic_id === id && item.section !== "past"),
            ),
          }
        : value.dev_card,
    });
  }
  return {
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
  };
}
