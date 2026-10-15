"use client";

import { useMemo, useState, type FormEvent, type ReactNode } from "react";
import Link from "next/link";
import { ResourcePanel } from "@devfeed/ui/resource-layout";
import { Input } from "@devfeed/ui/input";
import { Button } from "@devfeed/ui/button";
import { Select } from "@devfeed/ui/select";
import { Checkbox } from "@devfeed/ui/choice-controls";
import { ConnectorFields } from "@devfeed/ui/connector-fields";
import {
  ConnectionLogoField,
  type ConnectionLogoChange,
  type ConnectionLogoState,
} from "@devfeed/ui/connection-logo";
import { responseMappings } from "@devfeed/ui/connector-response-mappings";
import { usePartnerRequest, isPartnerRequestCancelled } from "@/lib/partner-request";
import { PartnerAccountPicker } from "./partner-account-picker";
import { PartnerApiResponse } from "./partner-api-response";
import type { Account } from "@/lib/types";
import type {
  ConnectorConfig,
  ConnectorPreviewOut,
  PartnerConnectorConfig,
  PartnerConnectionOut,
} from "@/lib/api/generated/models";
import {
  PartnerConnectorPreviewResponse,
  PartnerConnectionPreviewResponse,
} from "@/lib/api/generated/validators";

export type ConnectionFormInput = {
  account_id?: string;
  name: string;
  enabled: boolean;
  sync_interval_minutes?: number;
  connector?: PartnerConnectorConfig;
  credential?: string;
  logo_url?: string | null;
  logo_file?: File;
};

const limits = {
  max_pages: 100,
  requests_per_minute: 60,
  timeout_seconds: 15,
  max_response_bytes: 2000000,
};

function defaultConnector(): ConnectorConfig {
  return {
    base_url: "",
    list_path: "/products",
    items_paths: ["items"],
    fields: {
      external_id: ["id"],
      name: ["name"],
      product_url: ["url"],
      listing_url: ["listing_url"],
      tagline: ["tagline", "description"],
      pricing: ["pricing"],
    },
    pagination: {
      mode: "none",
      parameter: null,
      size_parameter: null,
      page_size: 10,
      start: 1,
      next_path: "nextCursor",
    },
    auth: { mode: "none", source: "connection", secret_ref: "", header: "X-API-Key" },
    ...limits,
  };
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="grid min-w-0 gap-1.5 text-sm font-medium">
      {label}
      {children}
      {hint && <span className="text-xs font-normal text-muted-foreground">{hint}</span>}
    </label>
  );
}

export function PartnerConnectionForm({
  connection,
  credentialsAvailable,
  csrfToken,
  cancelHref,
  saving,
  error,
  onSave,
  onUnavailable,
  onLogoSave,
}: {
  connection?: PartnerConnectionOut;
  credentialsAvailable: boolean;
  csrfToken: string;
  cancelHref: string;
  saving: boolean;
  error: string;
  onSave: (input: ConnectionFormInput) => Promise<void>;
  onUnavailable: () => void;
  onLogoSave?: (change: ConnectionLogoChange, signal: AbortSignal) => Promise<ConnectionLogoState>;
}) {
  const [account, setAccount] = useState<Account>();
  const [name, setName] = useState(connection?.name ?? "");
  const [logoUrl, setLogoUrl] = useState(connection?.logo_url ?? "");
  const [logoFile, setLogoFile] = useState<File | null>(null);
  const [logoChanged, setLogoChanged] = useState(false);
  const [logoError, setLogoError] = useState("");
  const [logoBusy, setLogoBusy] = useState(false);
  const [enabled, setEnabled] = useState(connection?.enabled ?? true);
  const [interval, setInterval] = useState(String(connection?.sync_interval_minutes ?? 360));
  const [connector, setConnectorState] = useState(connection?.connector ?? defaultConnector());
  const [connectorDirty, setConnectorDirty] = useState(false);
  const [credential, setCredential] = useState("");
  const [replaceCredential, setReplaceCredential] = useState(false);
  const [responseData, setResponseData] = useState<unknown>();
  const [preview, setPreview] = useState<ConnectorPreviewOut>();
  const [previewError, setPreviewError] = useState("");
  const [testing, setTesting] = useState(false);
  const [validation, setValidation] = useState("");
  const editable = !connection || connection.connector_editable;
  const auth = connector.auth?.mode ?? "none";
  const configured = connection?.credential_configured ?? false;
  const initialAuth = connection?.connector?.auth?.mode ?? "none";
  const authenticationChanged =
    auth !== initialAuth ||
    connector.base_url.trim() !== connection?.connector?.base_url ||
    (connector.auth?.header ?? "X-API-Key") !==
      (connection?.connector?.auth?.header ?? "X-API-Key");
  const needsCredential =
    editable && auth !== "none" && (!configured || replaceCredential || authenticationChanged);
  const credentialReady =
    auth === "none" || (credentialsAvailable && (!needsCredential || Boolean(credential.trim())));
  const intervalChanged = !connection || interval !== String(connection.sync_interval_minutes);
  const busy = saving || testing || logoBusy;
  const accountId = connection?.account_id ?? account?.id;
  const request = usePartnerRequest({ accountId, onUnavailable });
  const definition = useMemo<PartnerConnectorConfig>(
    () => ({
      ...connector,
      max_pages: Math.min(connector.max_pages ?? limits.max_pages, limits.max_pages),
      requests_per_minute: Math.min(
        connector.requests_per_minute ?? limits.requests_per_minute,
        limits.requests_per_minute,
      ),
      timeout_seconds: Math.min(
        connector.timeout_seconds ?? limits.timeout_seconds,
        limits.timeout_seconds,
      ),
      max_response_bytes: Math.min(
        connector.max_response_bytes ?? limits.max_response_bytes,
        limits.max_response_bytes,
      ),
      base_url: connector.base_url.trim(),
      list_path: connector.list_path ?? "/products",
      pagination: { ...defaultConnector().pagination, ...connector.pagination },
      fields: { ...defaultConnector().fields, ...connector.fields },
      auth: {
        mode: auth,
        source: "connection",
        secret_ref: "",
        header: connector.auth?.header ?? "X-API-Key",
      },
    }),
    [connector, auth],
  );
  const mappings =
    responseData === undefined
      ? []
      : responseMappings(definition, responseData, defaultConnector());

  function clearTest() {
    setPreview(undefined);
    setPreviewError("");
  }
  function setConnector(value: ConnectorConfig) {
    if (
      value.base_url.trim() !== connector.base_url.trim() ||
      (value.auth?.mode ?? "none") !== auth ||
      (value.auth?.header ?? "X-API-Key") !== (connector.auth?.header ?? "X-API-Key")
    ) {
      setCredential("");
    }
    setConnectorState(value);
    setConnectorDirty(true);
    clearTest();
  }

  async function test() {
    if (!accountId || busy || !credentialReady) return;
    setTesting(true);
    clearTest();
    try {
      const result = await request(
        `connections/${connection ? `${encodeURIComponent(connection.provider)}/` : ""}connector-preview`,
        {
          method: "POST",
          headers: { "content-type": "application/json", "x-csrf-token": csrfToken },
          body: JSON.stringify({
            connector: definition,
            ...(connection ? { expected_revision: connection.revision } : {}),
            ...(needsCredential ? { credential } : {}),
          }),
          parse: (value) =>
            connection
              ? PartnerConnectionPreviewResponse.parse(value)
              : PartnerConnectorPreviewResponse.parse(value),
          errorMessage:
            "Could not test this import connection. Check the API and authentication settings.",
          statusMessages: {
            409: "This connection changed. Refresh the page before testing again.",
            429: "Too many preview requests. Wait a minute and try again.",
          },
        },
      );
      setPreview(result);
    } catch (reason) {
      if (!isPartnerRequestCancelled(reason))
        setPreviewError(
          reason instanceof Error ? reason.message : "Could not test this import connection.",
        );
    } finally {
      setTesting(false);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || logoError) return;
    if (!connection && !account) {
      setValidation("Choose the partner account that owns this connection.");
      return;
    }
    const minutes = Number(interval);
    if (intervalChanged && (!Number.isInteger(minutes) || minutes < 15 || minutes > 10080)) {
      setValidation("Enter a whole number of minutes between 15 and 10,080.");
      return;
    }
    if (needsCredential && !credentialsAvailable) {
      setValidation(
        "Credential storage is unavailable. Keep the existing API and authentication settings, or try again later.",
      );
      return;
    }
    if (needsCredential && !credential.trim()) {
      setValidation("Enter the credential for this authentication method.");
      return;
    }
    setValidation("");
    await onSave({
      name: name.trim(),
      ...(!connection ? { account_id: account!.id } : {}),
      enabled,
      ...(logoChanged && !logoFile ? { logo_url: logoUrl.trim() || null } : {}),
      ...(logoFile ? { logo_file: logoFile } : {}),
      ...(intervalChanged ? { sync_interval_minutes: minutes } : {}),
      ...(editable && (!connection || connectorDirty) ? { connector: definition } : {}),
      ...(needsCredential ? { credential } : {}),
    });
  }

  const authentication = (
    <div className="space-y-3">
      <Field label="Authentication method">
        <Select
          label="Authentication method"
          value={auth}
          required
          disabled={busy}
          options={[
            { value: "none", label: "No authentication" },
            {
              value: "bearer",
              label: "Bearer token",
              disabled: !credentialsAvailable && auth !== "bearer",
            },
            {
              value: "api_key",
              label: "API key",
              disabled: !credentialsAvailable && auth !== "api_key",
            },
          ]}
          onChange={(value) => {
            if (value !== "none" && value !== "bearer" && value !== "api_key") return;
            setConnector({ ...connector, auth: { ...connector.auth, mode: value } });
            setCredential("");
            setReplaceCredential(false);
          }}
        />
      </Field>
      {auth === "api_key" && (
        <Field label="API key header">
          <Input
            value={connector.auth?.header ?? "X-API-Key"}
            onChange={(event) =>
              setConnector({
                ...connector,
                auth: { ...connector.auth, mode: auth, header: event.target.value },
              })
            }
            required
            maxLength={100}
          />
        </Field>
      )}
      {!credentialsAvailable && (
        <p className="text-sm text-muted-foreground">
          Authenticated imports are not configured on this DevFeed server. Public APIs remain
          available.
        </p>
      )}
      {configured && auth !== "none" && (
        <>
          <p className="text-sm text-muted-foreground">
            A credential is configured. Leaving it unchanged keeps the existing credential. Changing
            the API origin or authentication requires a replacement.
          </p>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={replaceCredential}
              disabled={busy || !credentialsAvailable}
              onCheckedChange={(checked) => {
                setReplaceCredential(checked === true);
                setCredential("");
                clearTest();
              }}
            />
            Replace credential
          </label>
        </>
      )}
      {configured && initialAuth !== "none" && auth === "none" && (
        <p role="status" className="text-sm text-muted-foreground">
          Saving without authentication removes the stored credential.
        </p>
      )}
      {needsCredential && (
        <Field
          label={auth === "bearer" ? "Bearer token" : "API key"}
          hint="Stored encrypted. DevFeed never returns this value to the portal."
        >
          <Input
            type="password"
            value={credential}
            onChange={(event) => {
              setCredential(event.target.value);
              clearTest();
            }}
            autoComplete="new-password"
            required
            maxLength={8192}
          />
        </Field>
      )}
    </div>
  );

  return (
    <div
      className={
        editable ? "grid items-start gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]" : "min-w-0"
      }
    >
      <form
        aria-label="Import connection settings"
        aria-busy={busy}
        onSubmit={(event) => void submit(event)}
        className="min-w-0 space-y-4 rounded-lg border bg-card p-4"
      >
        {(error || validation) && (
          <p role="alert" className="notice">
            {validation || error}
          </p>
        )}
        <fieldset disabled={busy} className="min-w-0 space-y-4">
          <div className="grid items-start gap-4 sm:grid-cols-2">
            {!connection && (
              <div className="min-w-0 sm:col-span-2">
                <PartnerAccountPicker
                  account={account}
                  onChange={(next) => {
                    setAccount(next);
                    clearTest();
                  }}
                  required
                  disabled={busy}
                  onUnavailable={onUnavailable}
                />
                <p className="mt-2 text-xs text-muted-foreground">
                  Imported products and reporting belong to this account. The account cannot be
                  changed after creation.
                </p>
              </div>
            )}
            <Field label="Connection name">
              <Input
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={200}
                required
                autoComplete="off"
              />
            </Field>
            <Field
              label="Sync interval (minutes)"
              hint="How often DevFeed checks for product updates."
            >
              <Input
                type="number"
                value={interval}
                onChange={(event) => setInterval(event.target.value)}
                min={intervalChanged ? 15 : 1}
                max={10080}
                step={1}
                required
              />
            </Field>
            <label className="flex items-center gap-2 text-sm sm:col-span-2">
              <Checkbox
                checked={enabled}
                onCheckedChange={(checked) => setEnabled(checked === true)}
                disabled={busy}
              />
              Enable scheduled imports
            </label>
            <div className="sm:col-span-2">
              <ConnectionLogoField
                name={name}
                value={logoUrl}
                file={logoFile}
                variants={connection?.logo_variants}
                savedUrl={connection?.logo_url}
                disabled={busy}
                onCommit={onLogoSave}
                onSaved={(saved) => {
                  setLogoUrl(saved.logo_url ?? "");
                  setLogoFile(null);
                  setLogoChanged(false);
                }}
                onBusyChange={setLogoBusy}
                onValidationChange={setLogoError}
                onChange={(url, file) => {
                  setLogoChanged(true);
                  setLogoUrl(url);
                  setLogoFile(file);
                }}
              />
            </div>
          </div>
          {editable ? (
            <ConnectorFields
              suggestPaginationParameters
              value={definition}
              defaults={defaultConnector()}
              onChange={setConnector}
              disabled={busy}
              mappings={mappings}
              authentication={authentication}
              limits={limits}
            />
          ) : (
            <ResourcePanel title="Managed integration">
              <p className="text-sm text-muted-foreground">
                The API configuration and credentials for this integration are managed by DevFeed.
                You can change its name and schedule, pause imports, or sync it now.
              </p>
            </ResourcePanel>
          )}
        </fieldset>
        {previewError && (
          <p role="alert" className="text-sm text-destructive">
            {previewError}
          </p>
        )}
        {preview && (
          <section
            role="status"
            aria-label="Import test result"
            className="space-y-3 rounded-lg border p-4 text-sm"
          >
            <p className="font-medium">
              {preview.discovered} {preview.discovered === 1 ? "product" : "products"} found
              {preview.has_next_page ? "; another page is available" : ""}.
            </p>
            <p className="text-xs text-muted-foreground">
              Preview only. No products were imported and no settings were saved.
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
          </section>
        )}
        <div className="flex flex-wrap justify-end gap-2 border-t pt-4">
          {editable && (
            <Button
              variant="outline"
              loading={testing}
              loadingText="Testing…"
              disabled={
                busy ||
                !accountId ||
                !credentialReady ||
                !definition.base_url ||
                !definition.list_path?.startsWith("/")
              }
              onClick={() => void test()}
            >
              Test import connection
            </Button>
          )}
          <Button variant="outline" asChild disabled={busy}>
            <Link href={cancelHref} prefetch={false}>
              Cancel
            </Link>
          </Button>
          <Button type="submit" loading={saving} disabled={testing || logoBusy || !!logoError}>
            {connection ? "Save changes" : "Create connection"}
          </Button>
        </div>
      </form>
      {editable && (
        <PartnerApiResponse
          accountId={accountId}
          connection={connection}
          connector={definition}
          credential={needsCredential ? credential : undefined}
          credentialReady={credentialReady}
          csrfToken={csrfToken}
          onUnavailable={onUnavailable}
          mappings={mappings}
          onResponse={setResponseData}
          disabled={busy}
        />
      )}
    </div>
  );
}
