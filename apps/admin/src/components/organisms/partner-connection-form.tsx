"use client";

import { useState, type FormEvent } from "react";
import { Input } from "@/components/atoms/input";
import { Button } from "@/components/atoms/button";
import { Field } from "@/components/molecules/field";
import { Select } from "@/components/molecules/select";
import { EntityPicker, type EntityPickerSource } from "@/components/molecules/entity-picker";
import { PartnerConnectorFields } from "./partner-connector-fields";
import { connectorDefaults } from "@/lib/partner-connectors";
import { RequestState } from "@/components/molecules/request-state";
import { useAdmin } from "@/components/molecules/admin-session";
import {
  accountsV1AdminPartnerAccountsGet,
  dashboardV1AdminPartnerAccountsAccountIdDashboardGet,
  adminPartnerConnectionCreate,
  adminPartnerConnectionUpdate,
  adminPartnerConnectorPreview,
} from "@/lib/api/generated/admin";
import type {
  ConnectionOut,
  PartnerProviderOut,
  ConnectorConfig,
  ConnectorPreviewOut,
} from "@/lib/api/generated/models";

const accountCatalog = { label: "Partner accounts", singular: "Account", title: "name" };
const partnerAccounts: EntityPickerSource = {
  key: "connection-owner-accounts",
  list: async ({ q, offset, limit }, signal) => {
    const page = await accountsV1AdminPartnerAccountsGet({ q, offset, limit }, { signal });
    return {
      ...page,
      offset: offset ?? 0,
      limit: limit ?? 25,
      items: page.items.map((account) => ({ ...account })),
    };
  },
  get: async (id, signal) => {
    const dashboard = await dashboardV1AdminPartnerAccountsAccountIdDashboardGet(id, undefined, {
      signal,
    });
    return { ...dashboard.account };
  },
};

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
  const [provider, setProvider] = useState(
    connection?.provider ?? providers[0]?.provider ?? "__custom__",
  );
  const [customId, setCustomId] = useState(connection?.provider ?? "");
  const [name, setName] = useState(connection?.name ?? providers[0]?.name ?? "");
  const [connector, setConnector] = useState<ConnectorConfig>(
    connection?.connector ?? connectorDefaults(provider === "nick-launches"),
  );
  const [testing, setTesting] = useState(false);
  const [preview, setPreview] = useState<ConnectorPreviewOut>();
  const identifier = provider === "__custom__" ? customId : provider;
  function changeConnector(value: ConnectorConfig) {
    setConnector(value);
    setPreview(undefined);
  }
  async function test() {
    setTesting(true);
    setError(undefined);
    setPreview(undefined);
    try {
      setPreview(
        await adminPartnerConnectorPreview(
          { provider: identifier, connector },
          { headers: { "X-CSRF-Token": admin.csrf_token } },
        ),
      );
    } catch (error) {
      setError(error instanceof Error ? error : new Error("Could not test connection."));
    } finally {
      setTesting(false);
    }
  }
  const [accountId, setAccountId] = useState(connection?.account_id ?? "");
  const [enabled, setEnabled] = useState(connection?.enabled ?? true);
  const [interval, setInterval] = useState(String(connection?.sync_interval_minutes ?? 360));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error>();

  async function save(event: FormEvent) {
    event.preventDefault();
    if (saving || testing) return;
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
            {
              account_id: accountId || null,
              enabled,
              sync_interval_minutes,
              expected_revision: connection.revision,
              name,
              connector,
            },
            options,
          )
        : await adminPartnerConnectionCreate(
            {
              provider: identifier,
              name,
              connector,
              account_id: accountId || null,
              enabled,
              sync_interval_minutes,
            },
            options,
          );
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
      aria-busy={saving || testing}
      onSubmit={(event) => void save(event)}
      className="space-y-6 rounded-lg border bg-card p-6"
    >
      <RequestState error={error} />
      <div className="grid gap-6 sm:grid-cols-2">
        <Field label="Partner" required disabled={saving || testing || !!connection}>
          {(control) => (
            <Select
              {...control}
              label="Partner"
              value={provider}
              onChange={(next) => {
                setProvider(next);
                setName(providers.find((item) => item.provider === next)?.name ?? "");
                changeConnector(connectorDefaults(next === "nick-launches"));
              }}
              options={[
                ...providers.map((item) => ({ value: item.provider, label: item.name })),
                ...(connection && !providers.some((item) => item.provider === connection.provider)
                  ? [{ value: connection.provider, label: connection.name }]
                  : []),
                ...(!connection ? [{ value: "__custom__", label: "Custom API" }] : []),
              ]}
            />
          )}
        </Field>
        <Field label="Account" name="account_id" disabled={saving || testing}>
          {(control) => (
            <EntityPicker
              {...control}
              source={partnerAccounts}
              catalog={accountCatalog}
              value={accountId}
              onChange={setAccountId}
            />
          )}
        </Field>
        <Field label="Name" required disabled={saving || testing}>
          {(control) => (
            <Input {...control} value={name} onChange={(event) => setName(event.target.value)} />
          )}
        </Field>
        {provider === "__custom__" && (
          <Field
            label="Identifier"
            required
            disabled={saving || testing}
            tooltip="Unique lowercase slug, for example shipyard."
          >
            {(control) => (
              <Input
                {...control}
                pattern="[a-z0-9]+(-[a-z0-9]+)*"
                maxLength={200}
                value={customId}
                onChange={(event) => setCustomId(event.target.value)}
              />
            )}
          </Field>
        )}
        <Field label="Sync interval (minutes)" required disabled={saving || testing}>
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
        <Field label="Enabled" disabled={saving || testing}>
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
      <PartnerConnectorFields
        key={provider}
        value={connector}
        onChange={changeConnector}
        disabled={saving || testing}
      />
      {preview && (
        <div role="status" className="space-y-3 rounded-lg border p-4 text-sm">
          <p>
            {preview.discovered} products found
            {preview.has_next_page ? "; another page is available" : ""}.
          </p>
          {preview.products.map((product) => (
            <div key={product.external_id} className="space-y-1 border-t pt-3">
              <p className="font-medium">{product.name}</p>
              <p className="break-all text-muted-foreground">{product.product_url}</p>
              <p>{product.description}</p>
            </div>
          ))}
          {preview.errors.map((message, index) => (
            <p key={index} className="text-destructive">
              {message}
            </p>
          ))}
          {!preview.products.length && !preview.errors.length && (
            <p>No products matched the filters.</p>
          )}
        </div>
      )}
      <div className="flex flex-wrap justify-end gap-2 border-t pt-5">
        <Button
          variant="outline"
          disabled={saving || testing || !identifier || !connector.base_url}
          loading={testing}
          loadingText="Testing…"
          onClick={() => void test()}
        >
          Test connection
        </Button>
        <Button variant="outline" disabled={saving || testing} onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" loading={saving} loadingText="Saving…" disabled={testing}>
          {connection ? "Save changes" : "Create partner"}
        </Button>
      </div>
    </form>
  );
}
