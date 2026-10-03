"use client";

import { useEffect, useRef, useState } from "react";
import { Upload, X } from "lucide-react";
import { AccountError, userRequest, type UserProfile } from "@/lib/user";
import { useUser } from "./user-account";

export function AvatarUpload({
  hasAvatar,
  onSaved,
  onBusy,
}: {
  hasAvatar: boolean;
  onSaved: (saved: UserProfile) => void;
  onBusy: (busy: boolean) => void;
}) {
  const { user, refreshProfile } = useUser();
  const input = useRef<HTMLInputElement>(null);
  const pending = useRef<AbortController | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => () => pending.current?.abort(), []);

  async function update(file?: File) {
    if (!user || busy) return;
    setError("");
    if (file && (!file.size || file.size > 5 * 1024 * 1024)) {
      setError("Choose an image under 5 MB.");
      return;
    }
    if (file && !["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
      setError("Choose a JPEG, PNG or WebP image.");
      return;
    }
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    onBusy(true);
    const body = file ? new FormData() : undefined;
    if (file) body!.append("file", file);
    try {
      const saved = await userRequest<UserProfile>("settings/profile/avatar", {
        method: file ? "POST" : "DELETE",
        headers: { "X-CSRF-Token": user.csrf_token },
        body,
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(45000)]),
      });
      if (controller.signal.aborted) return;
      onSaved(saved);
      refreshProfile();
    } catch (cause) {
      if (controller.signal.aborted) return;
      setError(
        cause instanceof AccountError && cause.status === 413
          ? "Choose an image under 5 MB."
          : cause instanceof AccountError && [415, 422].includes(cause.status)
            ? "Choose a valid JPEG, PNG or WebP image."
            : "Couldn’t update your avatar. Try again.",
      );
    } finally {
      if (!controller.signal.aborted) {
        setBusy(false);
        onBusy(false);
      }
    }
  }

  return (
    <div className="avatar-upload">
      <input
        ref={input}
        className="sr-only"
        type="file"
        aria-label="Avatar image"
        accept="image/jpeg,image/png,image/webp"
        disabled={busy}
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file) void update(file);
        }}
      />
      <div className="avatar-upload-actions">
        <button
          className="settings-button settings-button-ghost"
          type="button"
          disabled={busy}
          aria-busy={busy}
          onClick={() => input.current?.click()}
        >
          <Upload size={15} aria-hidden="true" />
          {busy ? "Updating…" : "Upload avatar"}
        </button>
        {hasAvatar && (
          <button
            className="settings-button settings-button-ghost"
            type="button"
            disabled={busy}
            onClick={() => void update()}
          >
            <X size={15} aria-hidden="true" />
            Remove
          </button>
        )}
      </div>
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
