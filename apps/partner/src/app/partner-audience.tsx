"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import {
  AudienceTargeting,
  audienceValue,
  hasAudience,
  experienceLevels,
  type Audience,
  type AudienceTopicLoader,
} from "@devfeed/ui/audience-targeting";
import { Button } from "@/components/atoms/button";
import type { Account } from "@/lib/types";

function validAudience(value: unknown): value is Audience {
  if (!value || typeof value !== "object") return false;
  const row = value as Audience;
  return (
    Array.isArray(row.experience_levels) &&
    row.experience_levels.every((level) => experienceLevels.some((item) => item.value === level)) &&
    Array.isArray(row.language_topic_ids) &&
    row.language_topic_ids.every((id) => typeof id === "string") &&
    Array.isArray(row.tool_topic_ids) &&
    row.tool_topic_ids.every((id) => typeof id === "string")
  );
}

export function PartnerAudience({
  account,
  csrfToken,
  onSaved,
  onUnavailable,
}: {
  account: Account;
  csrfToken: string;
  onSaved: (account: Account) => void;
  onUnavailable: () => void;
}) {
  const router = useRouter();
  const [audience, setAudience] = useState(() => audienceValue(account.audience));
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  const access = useRef({ router, onUnavailable });
  useEffect(() => {
    access.current = { router, onUnavailable };
  }, [router, onUnavailable]);
  useEffect(() => () => request.current?.abort(), []);
  const loadTopics = useCallback<AudienceTopicLoader>(
    async (dimension, params, signal) => {
      const query = new URLSearchParams({ dimension });
      for (const [key, value] of Object.entries(params))
        if (value !== undefined) query.set(key, String(value));
      const response = await fetch(
        `/api/v1/partner/accounts/${account.id}/audience-topics?${query}`,
        {
          cache: "no-store",
          signal,
        },
      );
      if (signal.aborted) throw new Error("Request cancelled.");
      if (response.status === 401) {
        access.current.router.replace("/login");
        access.current.onUnavailable();
      } else if (response.status === 404) access.current.onUnavailable();
      if (!response.ok) throw new Error("Could not load audience topics.");
      const page = await response.json();
      if (
        !page ||
        !Array.isArray(page.items) ||
        typeof page.total !== "number" ||
        page.items.some(
          (item: Record<string, unknown>) =>
            !item ||
            ["id", "name", "slug", "kind", "status"].some((key) => typeof item[key] !== "string"),
        )
      )
        throw new Error("Could not load audience topics.");
      return page;
    },
    [account.id],
  );

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving || !account.advanced_targeting) return;
    const controller = new AbortController();
    request.current = controller;
    setSaving(true);
    setMessage("");
    setError("");
    try {
      const response = await fetch(`/api/v1/partner/accounts/${account.id}/preferences`, {
        method: "PUT",
        headers: { "content-type": "application/json", "x-csrf-token": csrfToken },
        body: JSON.stringify({ audience }),
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      if (response.status === 401) {
        router.replace("/login");
        onUnavailable();
        return;
      }
      if (response.status === 404) {
        onUnavailable();
        return;
      }
      if (!response.ok)
        throw new Error(
          response.status === 403
            ? "Could not update this account. Refresh the page and try again."
            : "Could not save the audience. Check the selections and retry.",
        );
      const saved = (await response.json()) as Account;
      if (saved.id !== account.id || !validAudience(saved.audience))
        throw new Error("Could not confirm the saved audience. Refresh and try again.");
      if (!controller.signal.aborted) {
        onSaved(saved);
        setMessage("Audience saved.");
      }
    } catch (reason) {
      if (!controller.signal.aborted)
        setError(reason instanceof Error ? reason.message : "Could not save the audience.");
    } finally {
      if (!controller.signal.aborted) setSaving(false);
    }
  }
  return (
    <section aria-label="Audience targeting" className="mt-6 space-y-4 border-t pt-6">
      <div className="space-y-1">
        <h2>Audience targeting</h2>
        <p className="muted text-sm">
          Any match within a group qualifies. Selected groups must all match the reader’s profile.
        </p>
        {!account.advanced_targeting && (
          <p className="muted text-sm">
            Advanced targeting requires Platinum or Diamond.
            {hasAudience(audience) &&
              " Saved restrictions are retained. These placements stay disabled until the account has an eligible tier or an administrator explicitly clears the restrictions."}
          </p>
        )}
      </div>
      <form aria-label="Audience settings" aria-busy={saving} onSubmit={save} className="space-y-4">
        <AudienceTargeting
          value={audience}
          loadTopics={loadTopics}
          disabled={saving}
          onChange={
            account.advanced_targeting
              ? (value) => {
                  setAudience(value);
                  setMessage("");
                }
              : undefined
          }
        />
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        {message && (
          <p role="status" className="text-sm">
            {message}
          </p>
        )}
        {account.advanced_targeting && (
          <Button type="submit" loading={saving}>
            Save audience
          </Button>
        )}
      </form>
    </section>
  );
}
