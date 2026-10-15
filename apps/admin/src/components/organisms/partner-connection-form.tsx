"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { Input } from "@devfeed/ui/input";
import { Button } from "@devfeed/ui/button";
import { Field } from "@/components/molecules/field";
import { Select } from "@/components/molecules/select";
import { EntityPicker, type EntityPickerSource } from "@/components/molecules/entity-picker";
import { PartnerApiResponse } from "./partner-api-response";
import { ConnectorFields } from "@devfeed/ui/connector-fields";
import { ConnectionLogoField, type ConnectionLogoChange } from "@devfeed/ui/connection-logo";
import { responseMappings } from "@devfeed/ui/connector-response-mappings";
import { connectorDefaults } from "@/lib/partner-connectors";
import { ApiError } from "@/lib/api/client";
import { ValidationErrors } from "@/components/molecules/validation-errors";
import { RequestState } from "@/components/molecules/request-state";
import { useAdmin } from "@/components/molecules/admin-session";
import {
  accountsV1AdminPartnerAccountsGet,
  dashboardV1AdminPartnerAccountsAccountIdDashboardGet,
  adminPartnerConnectionCreate,
  adminPartnerConnectionUpdate,
  adminPartnerConnectionLogoUpload,
  adminPartnerConnectionLogoUpdate,
  adminPartnerConnectorPreview,
} from "@/lib/api/generated/admin";
import type {
  ConnectionOut,
  PartnerProviderOut,
  ConnectorConfig,
  ConnectorPreviewOut,
} from "@/lib/api/generated/models";

const accountCatalog = { label: "Partner accounts", singular: "Partner account", title: "name" };
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
  const form = useRef<HTMLFormElement>(null);
  const [provider, setProvider] = useState(
    connection?.provider ?? providers[0]?.provider ?? "__custom__",
  );
  const [customId, setCustomId] = useState(connection?.provider ?? "");
  const [name, setName] = useState(connection?.name ?? providers[0]?.name ?? "");
  const [savedDraft, setSavedDraft] = useState<ConnectionOut>();
  const current = savedDraft ?? connection;
  const [logoUrl, setLogoUrl] = useState(connection?.logo_url ?? "");
  const [logoFile, setLogoFile] = useState<File | null>(null);
  const [logoChanged, setLogoChanged] = useState(false);
  const [logoError, setLogoError] = useState("");
  const [logoBusy, setLogoBusy] = useState(false);
  const [connector, setConnector] = useState<ConnectorConfig>(
    connection?.connector ?? connectorDefaults(provider),
  );
  const [responseData, setResponseData] = useState<unknown>();
  const mappings =
    responseData === undefined
      ? []
      : responseMappings(connector, responseData, connectorDefaults());
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
      setError(error instanceof Error ? error : new Error("Could not test import connection."));
    } finally {
      setTesting(false);
    }
  }
  const [accountId, setAccountId] = useState(connection?.account_id ?? "");
  const [enabled, setEnabled] = useState(connection?.enabled ?? true);
  const [interval, setInterval] = useState(String(connection?.sync_interval_minutes ?? 360));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error>();
  const errors = error instanceof ApiError ? error.fields : {};
  const busy = saving || testing || logoBusy;
  useEffect(() => {
    const invalid = form.current?.querySelectorAll<HTMLElement>('[aria-invalid="true"]');
    invalid?.forEach((control) => {
      let parent = control.parentElement;
      while (parent) {
        if (parent instanceof HTMLDetailsElement) parent.open = true;
        parent = parent.parentElement;
      }
    });
    invalid?.[0]?.focus();
  }, [error]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy || logoError) return;
    if (!accountId) {
      setError(
        new ApiError(422, "Please correct the highlighted fields.", {
          account_id: "Select a partner account.",
        }),
      );
      return;
    }
    const sync_interval_minutes = Number(interval);
    if (
      !Number.isInteger(sync_interval_minutes) ||
      sync_interval_minutes < 1 ||
      sync_interval_minutes > 10080
    ) {
      setError(
        new ApiError(422, "Please correct the highlighted fields.", {
          sync_interval_minutes: "Enter a whole number of minutes between 1 and 10,080.",
        }),
      );
      return;
    }
    setSaving(true);
    setError(undefined);
    let settingsSaved = false;
    try {
      const options = { headers: { "X-CSRF-Token": admin.csrf_token } };
      const logo = logoChanged && !logoFile ? { logo_url: logoUrl.trim() || null } : {};
      let result = current
        ? await adminPartnerConnectionUpdate(
            current.provider,
            {
              account_id: accountId || null,
              enabled,
              sync_interval_minutes,
              expected_revision: current.revision,
              name,
              connector,
              ...logo,
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
              ...logo,
            },
            options,
          );
      setSavedDraft(result);
      settingsSaved = true;
      if (logoFile) {
        const uploaded = await adminPartnerConnectionLogoUpload(
          result.provider,
          { file: logoFile, expected_revision: result.revision },
          options,
        );
        if (
          uploaded.provider !== result.provider ||
          uploaded.account_id !== result.account_id ||
          !uploaded.logo_url ||
          !uploaded.logo_variants?.length ||
          uploaded.revision <= result.revision
        )
          throw new Error("Could not confirm the uploaded logo. Refresh before trying again.");
        setSavedDraft(uploaded);
        result = uploaded;
      }
      onSaved(result);
    } catch (error) {
      const reason =
        error instanceof Error
          ? error
          : new Error("Could not save import connection settings. Try again.");
      setError(
        settingsSaved
          ? new Error(
              `Connection settings saved, but the logo was not uploaded. ${reason.message} Save changes again to retry this connection.`,
            )
          : reason,
      );
    } finally {
      setSaving(false);
    }
  }

  async function saveLogo(change: ConnectionLogoChange, signal: AbortSignal) {
    if (!current) throw new Error("Create this connection before updating its logo.");
    const options = { signal, headers: { "X-CSRF-Token": admin.csrf_token } };
    const result =
      "file" in change
        ? await adminPartnerConnectionLogoUpload(
            current.provider,
            { file: change.file, expected_revision: current.revision },
            options,
          )
        : await adminPartnerConnectionLogoUpdate(
            current.provider,
            { logo_url: change.logo_url, expected_revision: current.revision },
            options,
          );
    if (
      result.provider !== current.provider ||
      result.account_id !== current.account_id ||
      result.revision < current.revision
    )
      throw new Error("Could not confirm the updated logo. Refresh before trying again.");
    if (
      ("file" in change || change.logo_url) &&
      (!result.logo_url || !result.logo_variants?.length)
    )
      throw new Error("Could not confirm the uploaded logo. Refresh before trying again.");
    if (!("file" in change) && change.logo_url === null && result.logo_url !== null)
      throw new Error("Could not confirm logo removal. Refresh before trying again.");
    setSavedDraft(result);
    return result;
  }

  return (
    <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
      <form
        ref={form}
        aria-label="Import connection settings"
        aria-busy={busy}
        onSubmit={(event) => void save(event)}
        className="space-y-6 rounded-lg border bg-card p-6"
      >
        <RequestState error={error} />
        <ValidationErrors error={error} />
        <div className="grid gap-6 sm:grid-cols-2">
          <Field
            label="Platform"
            required
            disabled={busy || !!current}
            error={provider === "__custom__" ? undefined : errors.provider}
          >
            {(control) => (
              <Select
                {...control}
                label="Platform"
                value={provider}
                onChange={(next) => {
                  setProvider(next);
                  setName(providers.find((item) => item.provider === next)?.name ?? "");
                  changeConnector(connectorDefaults(next));
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
          <Field
            label="Partner account"
            name="account_id"
            required
            disabled={busy}
            error={errors.account_id}
            tooltip="Imported products belong to this account and inherit its promotion settings."
          >
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
          <Field
            label="Name"
            required
            disabled={busy}
            error={errors.name}
            className="sm:col-span-2"
          >
            {(control) => (
              <Input {...control} value={name} onChange={(event) => setName(event.target.value)} />
            )}
          </Field>

          {provider === "__custom__" && (
            <Field
              label="Identifier"
              error={errors.provider}
              required
              disabled={busy}
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
          <Field
            label="Sync interval (minutes)"
            required
            disabled={busy}
            error={errors.sync_interval_minutes}
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
          <Field label="Enabled" disabled={busy} error={errors.enabled}>
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
        <div>
          <ConnectionLogoField
            name={name}
            value={logoUrl}
            file={logoFile}
            variants={current?.logo_variants}
            savedUrl={current?.logo_url}
            disabled={busy}
            onCommit={current ? saveLogo : undefined}
            onSaved={(saved) => {
              setLogoUrl(saved.logo_url ?? "");
              setLogoFile(null);
              setLogoChanged(false);
            }}
            onBusyChange={setLogoBusy}
            onValidationChange={setLogoError}
            error={errors.logo_url}
            onChange={(url, file) => {
              setLogoChanged(true);
              setLogoUrl(url);
              setLogoFile(file);
            }}
          />
        </div>
        <ConnectorFields
          key={provider}
          Field={Field}
          defaults={connectorDefaults()}
          value={connector}
          errors={errors}
          mappings={mappings}
          onChange={changeConnector}
          disabled={busy}
        />
        {preview && (
          <div role="status" className="space-y-3 rounded-lg border p-4 text-sm">
            <p>
              {preview.discovered} {preview.discovered === 1 ? "product" : "products"} found
              {preview.has_next_page ? "; another page is available" : ""}.
            </p>
            {preview.products.map((product) => (
              <div key={product.external_id} className="space-y-1 border-t pt-3">
                <p className="font-medium">{product.name}</p>
                <p className="break-all text-muted-foreground">{product.product_url}</p>
                <p>{product.tagline}</p>
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
            disabled={busy || !identifier || !connector.base_url}
            loading={testing}
            loadingText="Testing…"
            onClick={() => void test()}
          >
            Test import connection
          </Button>
          <Button variant="outline" disabled={busy} onClick={onCancel}>
            Cancel
          </Button>
          <Button
            type="submit"
            loading={saving}
            loadingText="Saving…"
            disabled={testing || logoBusy || !!logoError}
          >
            {current ? "Save changes" : "Create import connection"}
          </Button>
        </div>
      </form>
      <PartnerApiResponse
        provider={identifier}
        connector={connector}
        mappings={mappings}
        onResponse={setResponseData}
      />
    </div>
  );
}
