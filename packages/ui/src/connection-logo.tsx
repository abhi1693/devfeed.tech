"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ImageIcon, Upload, X } from "lucide-react";
import { Button } from "./button";
import { Input } from "./input";

export type ConnectionLogoVariant = { url: string; width: number };

function imageUrl(value: string | null | undefined) {
  if (!value || value.length > 2048 || /[\s\u0000-\u001f\u007f]/.test(value)) return undefined;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password ? url.href : undefined;
  } catch {
    return undefined;
  }
}

/** Only pass the managed presentation URL, never the editable remote source URL. */
export function ConnectionLogo({
  name,
  url,
  variants = [],
  size = 32,
}: {
  name: string;
  url?: string | null;
  variants?: readonly ConnectionLogoVariant[];
  size?: number;
}) {
  const source = imageUrl(url);
  const [failed, setFailed] = useState<string>();
  const srcSet = variants
    .filter(
      (variant) => imageUrl(variant.url) && Number.isInteger(variant.width) && variant.width > 0,
    )
    .map((variant) => `${imageUrl(variant.url)} ${variant.width}w`)
    .join(", ");
  return (
    <span
      aria-hidden="true"
      className="inline-flex shrink-0 items-center justify-center overflow-hidden rounded-md bg-muted text-xs font-semibold text-muted-foreground"
      style={{ width: size, height: size }}
    >
      {source && failed !== source ? (
        // Managed variants load directly without another server-side image request.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={source}
          srcSet={srcSet || undefined}
          sizes={`${size}px`}
          width={size}
          height={size}
          alt=""
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          onError={() => setFailed(source)}
          className="size-full object-contain"
        />
      ) : (
        name.trim().slice(0, 2).toUpperCase() || "LP"
      )}
    </span>
  );
}

export type ConnectionLogoChange = { logo_url: string | null } | { file: File };
export type ConnectionLogoState = {
  logo_url?: string | null;
  logo_variants?: readonly ConnectionLogoVariant[];
};

export function ConnectionLogoField({
  name,
  value,
  file,
  savedUrl,
  variants,
  disabled = false,
  error,
  onChange,
  onCommit,
  onSaved,
  onBusyChange,
  onValidationChange,
}: {
  name: string;
  value: string;
  file: File | null;
  savedUrl?: string | null;
  variants?: readonly ConnectionLogoVariant[];
  disabled?: boolean;
  error?: string;
  onChange: (url: string, file: File | null) => void;
  onCommit?: (change: ConnectionLogoChange, signal: AbortSignal) => Promise<ConnectionLogoState>;
  onSaved?: (saved: ConnectionLogoState) => void;
  onBusyChange?: (busy: boolean) => void;
  onValidationChange?: (message: string) => void;
}) {
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const pending = useRef<AbortController | null>(null);
  const [fileError, setFileError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [status, setStatus] = useState("");
  const [failed, setFailed] = useState<ConnectionLogoChange | null>(null);
  const [preview, setPreview] = useState<{ file: File; url: string } | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  useEffect(
    () => () => {
      if (preview) URL.revokeObjectURL(preview.url);
    },
    [preview],
  );
  const message = fileError || error;
  const busy = disabled || uploading;
  function validate(message: string) {
    setFileError(message);
    onValidationChange?.(message);
  }
  async function commit(change: ConnectionLogoChange) {
    if (!onCommit || pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setUploading(true);
    onBusyChange?.(true);
    validate("");
    setStatus("");
    setFailed(null);
    try {
      const saved = await onCommit(change, controller.signal);
      if (controller.signal.aborted) return;
      validate("");
      if (onSaved) onSaved(saved);
      else onChange(saved.logo_url ?? "", null);
      setPreview(null);
      setStatus(saved.logo_url ? "Logo updated." : "Logo removed.");
    } catch (reason) {
      if (controller.signal.aborted) return;
      validate(reason instanceof Error ? reason.message : "Could not update the logo. Try again.");
      setFailed(change);
    } finally {
      if (pending.current === controller) pending.current = null;
      if (!controller.signal.aborted) {
        setUploading(false);
        onBusyChange?.(false);
      }
    }
  }
  function clear() {
    validate("");
    setPreview(null);
    setFailed(null);
    setStatus("");
    onChange("", null);
    if (onCommit && savedUrl) void commit({ logo_url: null });
  }
  function saveUrl() {
    if (file) return;
    const url = value.trim();
    if (url && !imageUrl(url)) {
      validate("Enter a public HTTPS logo URL.");
      return;
    }
    if (onCommit && url !== (savedUrl ?? "")) void commit({ logo_url: url || null });
  }
  return (
    <fieldset
      disabled={busy}
      aria-busy={uploading}
      className="min-w-0 space-y-3 rounded-lg border bg-muted/20 p-4"
      onBlur={(event) => {
        const next = event.relatedTarget;
        if (next instanceof HTMLElement && event.currentTarget.contains(next)) return;
        // Logo actions and the form's submit handler consume the current draft themselves.
        if (
          (next instanceof HTMLButtonElement || next instanceof HTMLInputElement) &&
          ["submit", "image"].includes(next.type) &&
          event.currentTarget.form &&
          next.form === event.currentTarget.form
        )
          return;
        saveUrl();
      }}
    >
      <legend className="px-1 text-sm font-medium">Platform logo</legend>
      <div className="flex min-w-0 items-start gap-4">
        <div
          role="img"
          aria-label="Platform logo preview"
          className="flex size-16 shrink-0 items-center justify-center overflow-hidden rounded-lg border bg-card text-muted-foreground"
        >
          {file && preview?.file === file ? (
            // The owner's selected draft stays local while its upload is pending.
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={preview.url}
              alt=""
              className="size-full object-contain p-1"
              onError={() => validate("Choose a valid JPEG, PNG or WebP image.")}
            />
          ) : savedUrl ? (
            <ConnectionLogo name={name} url={savedUrl} variants={variants} size={56} />
          ) : (
            <ImageIcon size={24} aria-hidden="true" />
          )}
        </div>
        <div className="min-w-0 flex-1 space-y-2">
          <label htmlFor={id} className="sr-only">
            Logo URL
          </label>
          <Input
            id={id}
            type="url"
            value={value}
            maxLength={2048}
            pattern="https://.*"
            placeholder="https://example.com/logo.png"
            autoComplete="off"
            disabled={busy}
            aria-invalid={!!message}
            aria-describedby={`${id}-help${message ? ` ${id}-error` : ""}`}
            onChange={(event) => {
              validate("");
              setPreview(null);
              setFailed(null);
              setStatus("");
              onChange(event.target.value, null);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" && onCommit && value.trim() !== (savedUrl ?? "")) {
                event.preventDefault();
                event.currentTarget.blur();
              }
            }}
          />
          <input
            ref={input}
            type="file"
            tabIndex={-1}
            className="sr-only"
            aria-label="Logo image file"
            accept="image/jpeg,image/png,image/webp"
            disabled={busy}
            onChange={(event) => {
              const selected = event.target.files?.[0];
              event.target.value = "";
              if (!selected) return;
              if (!selected.size || selected.size > 5 * 1024 * 1024) {
                validate("Choose an image under 5 MB.");
                return;
              }
              if (!["image/jpeg", "image/png", "image/webp"].includes(selected.type)) {
                validate("Choose a JPEG, PNG or WebP image.");
                return;
              }
              validate("");
              setFailed(null);
              setStatus("");
              try {
                setPreview({ file: selected, url: URL.createObjectURL(selected) });
              } catch {
                setPreview(null);
              }
              onChange("", selected);
              if (onCommit) void commit({ file: selected });
            }}
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => input.current?.click()}
            >
              <Upload size={14} aria-hidden="true" />
              {uploading ? "Updating…" : "Upload logo"}
            </Button>
            {(value || file || savedUrl || fileError) && (
              <Button
                variant="ghost"
                size="sm"
                disabled={busy}
                onClick={clear}
                className="px-2 text-xs"
              >
                <X size={12} aria-hidden="true" />
                Remove logo
              </Button>
            )}
            {failed && (
              <Button
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() => void commit(failed)}
              >
                Retry logo
              </Button>
            )}
          </div>
          <p id={`${id}-help`} className="text-xs leading-5 text-muted-foreground">
            JPEG, PNG or WebP · Up to 5 MB
          </p>
          {!onCommit && (
            <p className="text-xs text-muted-foreground">
              Your logo will be saved when you create this connection.
            </p>
          )}
          {file && <p className="break-all text-xs text-muted-foreground">{file.name}</p>}
          {(status || uploading) && (
            <p role="status" className="text-xs text-muted-foreground">
              {uploading ? "Updating logo…" : status}
            </p>
          )}
          {message && (
            <p id={`${id}-error`} role="alert" className="text-sm text-destructive">
              {message}
            </p>
          )}
        </div>
      </div>
    </fieldset>
  );
}
