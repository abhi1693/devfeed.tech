"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";
import { FormField } from "@/components/molecules/form-field";
import { useAdmin } from "@/components/molecules/admin-session";
import { adminSourcePreview } from "@/lib/api/generated/admin";
import type { SourcePreviewRequest } from "@/lib/api/generated/models";
import { ApiError } from "@/lib/api/client";
import { notify, notifyFailure } from "@/lib/notifications";
import { resources } from "@/lib/resources";

const profileFields = [
  "name",
  "description",
  "website_url",
  "language",
  "logo_url",
  "image_url",
] as const;
type Values = Record<string, unknown>;

function validFeedUrl(value: string) {
  try {
    const url = new URL(value);
    return (
      ["https:", "http:"].includes(url.protocol) && !!url.hostname && !url.username && !url.password
    );
  } catch {
    return false;
  }
}

export function SourceFormFields({
  values,
  onValuesChange,
  editing,
  disabled = false,
  errors = {},
  onPreviewBusyChange,
}: {
  values: Values;
  onValuesChange: Dispatch<SetStateAction<Values>>;
  editing: boolean;
  disabled?: boolean;
  errors?: Record<string, string>;
  onPreviewBusyChange: (busy: boolean) => void;
}) {
  const admin = useAdmin();
  const request = useRef<AbortController | null>(null);
  const lastAttempt = useRef<string | null>(null);
  const edited = useRef(new Set<string>());
  const filled = useRef<Record<string, string>>({});
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<Error>();
  const feedUrl = String(values.feed_url ?? "").trim();
  const sourceType = values.source_type as SourcePreviewRequest["source_type"];

  useEffect(
    () => () => {
      request.current?.abort();
      request.current = null;
    },
    [],
  );

  function change(key: string, value: unknown) {
    if (key === "feed_url" || key === "source_type") {
      setError(undefined);
      request.current?.abort();
      request.current = null;
      lastAttempt.current = null;
      setPending(false);
      onPreviewBusyChange(false);
      const previousFill = filled.current;
      filled.current = {};
      onValuesChange((previous) => {
        const next = { ...previous, [key]: value };
        // A different feed must not inherit the previous feed's branding.
        // Deliberate edits, including intentionally cleared fields, are kept.
        for (const [field, candidate] of Object.entries(previousFill)) {
          if (!edited.current.has(field) && next[field] === candidate) next[field] = "";
        }
        return next;
      });
    } else {
      edited.current.add(key);
      delete filled.current[key];
      onValuesChange((previous) => ({ ...previous, [key]: value }));
    }
  }

  const lookup = useCallback(async () => {
    const identity = `${sourceType}\n${feedUrl}`;
    if (disabled || request.current || lastAttempt.current === identity) return;
    if (!validFeedUrl(feedUrl) || !sourceType) return;
    lastAttempt.current = identity;
    const controller = new AbortController();
    request.current = controller;
    setPending(true);
    onPreviewBusyChange(true);
    setError(undefined);
    try {
      const details = await adminSourcePreview(
        {
          feed_url: feedUrl,
          source_type: sourceType,
        },
        { signal: controller.signal, headers: { "X-CSRF-Token": admin.csrf_token } },
      );
      if (request.current !== controller) return;
      onValuesChange((previous) => {
        const next = { ...previous };
        for (const field of profileFields) {
          const candidate = details[field];
          if (candidate && !edited.current.has(field) && !String(next[field] ?? "").trim()) {
            next[field] = candidate;
            filled.current[field] = candidate;
          }
        }
        return next;
      });
      if (details.warnings.length)
        notify.warning("Some source details could not be fetched", {
          description: details.warnings.join("\n"),
          id: "source-preview-warning",
        });
    } catch (error) {
      if (request.current !== controller) return;
      notifyFailure(error, "Could not fetch source details", "source-preview-error");
      setError(error instanceof Error ? error : new Error("Could not fetch source details."));
    } finally {
      if (request.current === controller) {
        request.current = null;
        setPending(false);
        onPreviewBusyChange(false);
      }
    }
  }, [admin.csrf_token, disabled, feedUrl, sourceType, onValuesChange, onPreviewBusyChange]);

  useEffect(() => {
    if (editing || disabled || !validFeedUrl(feedUrl) || !sourceType) return;
    const timer = setTimeout(() => {
      void lookup();
    }, 650);
    return () => clearTimeout(timer);
  }, [editing, disabled, feedUrl, sourceType, lookup]);

  function field(key: string) {
    const spec = resources.sources.fields.find((item) => item.key === key)!;
    return (
      <FormField
        field={{ ...spec, required: spec.required || (editing && key === "name") }}
        value={values[key]}
        onChange={(value) => change(key, value)}
        disabled={editing && spec.createOnly}
        loading={key === "feed_url" ? pending : undefined}
        loadingText="Checking the feed and its website…"
        error={error instanceof ApiError ? (error.fields[key] ?? errors[key]) : errors[key]}
      />
    );
  }

  return (
    <div className="space-y-7">
      <section aria-labelledby="source-feed-heading" className="space-y-5">
        <div>
          <h2 id="source-feed-heading" className="font-semibold">
            Feed setup
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            {editing
              ? "The feed URL and source type cannot be changed after creation."
              : "Details are fetched automatically after you enter an RSS or Atom URL. Your edits are kept."}
          </p>
        </div>
        {field("feed_url")}
        <div className="sm:max-w-md">{field("source_type")}</div>
      </section>

      <section aria-labelledby="source-profile-heading" className="space-y-5 border-t pt-6">
        <div>
          <h2 id="source-profile-heading" className="font-semibold">
            Source profile
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Review how this source will appear in the app. Missing details can be entered manually.
          </p>
        </div>
        <div className="grid gap-5 sm:grid-cols-2">
          {field("name")}
          {field("language")}
          <div className="sm:col-span-2">{field("description")}</div>
          <div className="sm:col-span-2">{field("website_url")}</div>
          {field("logo_url")}
          {field("image_url")}
        </div>
      </section>

      <section aria-labelledby="source-polling-heading" className="space-y-5 border-t pt-6">
        <div>
          <h2 id="source-polling-heading" className="font-semibold">
            Polling settings
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Applied when you save. Fetching details does not start ingestion.
          </p>
        </div>
        <div className="grid items-start gap-5 sm:grid-cols-2">
          {field("enabled")}
          {field("poll_interval_seconds")}
        </div>
      </section>
    </div>
  );
}
