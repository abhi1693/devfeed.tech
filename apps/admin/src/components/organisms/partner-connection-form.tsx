"use client";

import { useState, type FormEvent } from "react";
import { Input } from "@/components/atoms/input";
import { Button } from "@/components/atoms/button";
import { Field } from "@/components/molecules/field";
import { Select } from "@/components/molecules/select";
import { RequestState } from "@/components/molecules/request-state";
import { useAdmin } from "@/components/molecules/admin-session";
import {
  adminPartnerConnectionCreate,
  adminPartnerConnectionUpdate,
} from "@/lib/api/generated/admin";
import type { ConnectionOut, PartnerProviderOut } from "@/lib/api/generated/models";

export function PartnerConnectionForm({
  providers,
  connection,
  onSaved,
  onCancel,
}: {
  providers: PartnerProviderOut[];
  connection?: ConnectionOut;
  onSaved: (connection: ConnectionOut) => void;
  onCancel: () => void;
}) {
  const admin = useAdmin();
  const [provider, setProvider] = useState(connection?.provider ?? providers[0]?.provider ?? "");
  const [enabled, setEnabled] = useState(connection?.enabled ?? true);
  const [interval, setInterval] = useState(String(connection?.sync_interval_minutes ?? 360));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error>();
  const selectedProvider = providers.find((item) => item.provider === provider);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (saving || !selectedProvider) return;
    const sync_interval_minutes = Number(interval);
    if (
      !Number.isInteger(sync_interval_minutes) ||
      sync_interval_minutes < 1 ||
      sync_interval_minutes > 10080
    ) {
      setError(new Error("Enter a whole number of minutes between 1 and 10,080."));
      return;
    }
    setSaving(true);
    setError(undefined);
    try {
      const options = { headers: { "X-CSRF-Token": admin.csrf_token } };
      const result = connection
        ? await adminPartnerConnectionUpdate(
            connection.provider,
            { enabled, sync_interval_minutes, expected_revision: connection.revision },
            options,
          )
        : await adminPartnerConnectionCreate({ provider, enabled, sync_interval_minutes }, options);
      onSaved(result);
    } catch (error) {
      setError(
        error instanceof Error ? error : new Error("Could not save partner settings. Try again."),
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      aria-label="Partner settings"
      aria-busy={saving}
      onSubmit={(event) => void save(event)}
      className="space-y-6 rounded-lg border bg-card p-6"
    >
      <RequestState error={error} />
      <div className="grid gap-6 sm:grid-cols-2">
        <Field
          label="Partner"
          required
          disabled={saving || !!connection}
          subtext={selectedProvider?.description}
        >
          {(control) => (
            <Select
              {...control}
              label="Partner"
              value={provider}
              onChange={setProvider}
              options={providers.map((item) => ({
                value: item.provider,
                label: item.name,
                description:
                  item.partnership_type === "launch_platform"
                    ? "Launch platform"
                    : item.partnership_type,
              }))}
            />
          )}
        </Field>
        <Field
          label="Sync interval (minutes)"
          required
          disabled={saving}
          subtext="From 1 minute to 7 days (10,080 minutes). Applies to future syncs; running jobs continue."
        >
          {(control) => (
            <Input
              {...control}
              type="number"
              min={1}
              max={10080}
              step={1}
              value={interval}
              onChange={(event) => setInterval(event.target.value)}
            />
          )}
        </Field>
        <Field
          label="Enabled"
          disabled={saving}
          subtext="Sync products and run automatic checks while enabled."
        >
          {(control) => (
            <input
              {...control}
              type="checkbox"
              checked={enabled}
              onChange={(event) => setEnabled(event.target.checked)}
              className="size-4 accent-primary"
            />
          )}
        </Field>
      </div>
      <div className="flex flex-wrap justify-end gap-2 border-t pt-5">
        <Button variant="outline" disabled={saving} onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" loading={saving} loadingText="Saving…" disabled={!selectedProvider}>
          {connection ? "Save changes" : "Create partner"}
        </Button>
      </div>
    </form>
  );
}
