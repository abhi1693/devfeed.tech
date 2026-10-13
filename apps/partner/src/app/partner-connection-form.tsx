"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import Link from "next/link";
import { ResourcePanel } from "@devfeed/ui/resource-layout";
import { Input } from "@devfeed/ui/input";
import { Button } from "@devfeed/ui/button";
import { Select } from "@devfeed/ui/select";
import { Checkbox } from "@devfeed/ui/choice-controls";
import { PartnerAccountPicker } from "./partner-account-picker";
import type { Account } from "@/lib/types";
import type {
  ConnectorConfig,
  PartnerConnectorConfig,
  PartnerConnectionOut,
} from "@/lib/api/generated/models";

export type ConnectionFormInput = {
  account_id?: string;
  name: string;
  enabled: boolean;
  sync_interval_minutes?: number;
  connector?: PartnerConnectorConfig;
  credential?: string;
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
      description: ["description"],
      pricing: ["pricing"],
    },
    pagination: { mode: "none", parameter: null, size_parameter: null },
    auth: { mode: "none" },
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

const mappingFields = [
  ["external_id", "Product ID paths"],
  ["name", "Product name paths"],
  ["product_url", "Product website paths"],
  ["listing_url", "Listing URL paths"],
  ["description", "Description paths"],
  ["pricing", "Pricing paths"],
] as const;

export function PartnerConnectionForm({
  connection,
  credentialsAvailable,
  cancelHref,
  saving,
  error,
  onSave,
  onUnavailable,
}: {
  connection?: PartnerConnectionOut;
  credentialsAvailable: boolean;
  cancelHref: string;
  saving: boolean;
  error: string;
  onSave: (input: ConnectionFormInput) => Promise<void>;
  onUnavailable: () => void;
}) {
  const [account, setAccount] = useState<Account>();
  const [name, setName] = useState(connection?.name ?? "");
  const [enabled, setEnabled] = useState(connection?.enabled ?? true);
  const [interval, setInterval] = useState(String(connection?.sync_interval_minutes ?? 360));
  const [connector, setConnectorState] = useState(connection?.connector ?? defaultConnector());
  const [connectorDirty, setConnectorDirty] = useState(false);
  function setConnector(value: ConnectorConfig) {
    setConnectorState(value);
    setConnectorDirty(true);
  }
  const [mapping, setMapping] = useState(
    Object.fromEntries(
      mappingFields.map(([key]) => [key, (connector.fields?.[key] ?? [key]).join(", ")]),
    ),
  );
  const [itemsPaths, setItemsPaths] = useState((connector.items_paths ?? ["items"]).join(", "));
  const [credential, setCredential] = useState("");
  const [replaceCredential, setReplaceCredential] = useState(false);
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
  const intervalChanged = !connection || interval !== String(connection.sync_interval_minutes);
  const [validation, setValidation] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
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
    const paths = (value: string) =>
      value
        .split(",")
        .map((part) => part.trim())
        .filter(Boolean);
    await onSave({
      name: name.trim(),
      ...(!connection ? { account_id: account!.id } : {}),
      enabled,
      ...(intervalChanged ? { sync_interval_minutes: minutes } : {}),
      ...(editable && (!connection || connectorDirty)
        ? {
            connector: {
              ...connector,
              max_pages: Math.min(connector.max_pages ?? 100, 100),
              requests_per_minute: Math.min(connector.requests_per_minute ?? 60, 60),
              timeout_seconds: Math.min(connector.timeout_seconds ?? 15, 15),
              max_response_bytes: Math.min(connector.max_response_bytes ?? 2000000, 2000000),
              base_url: connector.base_url.trim(),
              items_paths: itemsPaths.trim() ? paths(itemsPaths) : [""],
              fields: Object.fromEntries(
                mappingFields.map(([key]) => [key, paths(mapping[key] ?? "")]),
              ),
              auth: {
                mode: auth,
                source: "connection",
                secret_ref: "",
                header: connector.auth?.header ?? "X-API-Key",
              },
            },
          }
        : {}),
      ...(needsCredential ? { credential } : {}),
    });
  }

  return (
    <form onSubmit={(event) => void submit(event)} className="space-y-4">
      {(error || validation) && (
        <p role="alert" className="notice">
          {validation || error}
        </p>
      )}
      <fieldset disabled={saving} className="min-w-0 space-y-4">
        <div className={connection ? "min-w-0" : "grid items-start gap-4 lg:grid-cols-2"}>
          {!connection && (
            <ResourcePanel title="Partner account">
              <div className="max-w-xl">
                <PartnerAccountPicker
                  account={account}
                  onChange={setAccount}
                  required
                  disabled={saving}
                  onUnavailable={onUnavailable}
                />
              </div>
              <p className="mt-3 text-sm text-muted-foreground">
                Imported products and reporting belong to this account. The account cannot be
                changed after creation.
              </p>
            </ResourcePanel>
          )}
          <ResourcePanel title="Connection settings">
            <div className="grid gap-4 sm:grid-cols-2">
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
            </div>
            <label className="mt-4 flex items-center gap-2 text-sm">
              <Checkbox
                checked={enabled}
                onCheckedChange={(checked) => setEnabled(checked === true)}
                disabled={saving}
              />
              Enable scheduled imports
            </label>
          </ResourcePanel>
        </div>
        {editable ? (
          <div className="grid items-start gap-4 lg:grid-cols-2">
            <ResourcePanel title="Product API">
              <p className="mb-3 text-sm text-muted-foreground">
                Self-service imports use up to 100 pages per sync, 60 requests per minute and a
                15-second response deadline.
              </p>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field
                  label="API base URL"
                  hint="Public HTTPS origin, for example https://api.example.com."
                >
                  <Input
                    type="url"
                    value={connector.base_url}
                    onChange={(event) =>
                      setConnector({ ...connector, base_url: event.target.value })
                    }
                    placeholder="https://api.example.com"
                    required
                  />
                </Field>
                <Field
                  label="Products endpoint"
                  hint="Path on this origin, for example /v1/products."
                >
                  <Input
                    value={connector.list_path ?? "/products"}
                    onChange={(event) =>
                      setConnector({ ...connector, list_path: event.target.value })
                    }
                    maxLength={500}
                    required
                  />
                </Field>
                <Field
                  label="Product list paths"
                  hint="Comma-separated JSON paths. Leave blank for a top-level array."
                >
                  <Input
                    value={itemsPaths}
                    onChange={(event) => {
                      setItemsPaths(event.target.value);
                      setConnectorDirty(true);
                    }}
                    placeholder="items, data.products"
                  />
                </Field>
                <Field
                  label="Product detail endpoint"
                  hint="Optional path containing {id}, when the list omits full details."
                >
                  <Input
                    value={connector.detail_path ?? ""}
                    onChange={(event) =>
                      setConnector({ ...connector, detail_path: event.target.value || null })
                    }
                    maxLength={500}
                    placeholder="/products/{id}"
                  />
                </Field>
              </div>
            </ResourcePanel>
            <ResourcePanel title="Product field mapping">
              <p className="mb-3 text-sm text-muted-foreground">
                Map each product fact to JSON paths. Separate alternatives with commas; the first
                populated field is used.
              </p>
              <div className="grid gap-4 sm:grid-cols-2">
                {mappingFields.map(([key, label]) => (
                  <Field key={key} label={label}>
                    <Input
                      value={mapping[key] ?? ""}
                      onChange={(event) => {
                        setMapping({ ...mapping, [key]: event.target.value });
                        setConnectorDirty(true);
                      }}
                      required
                    />
                  </Field>
                ))}
              </div>
            </ResourcePanel>
            <ResourcePanel title="Authentication">
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Authentication method">
                  <Select
                    label="Authentication method"
                    value={auth}
                    required
                    disabled={saving}
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
                      setConnector({
                        ...connector,
                        auth: {
                          ...connector.auth,
                          mode: value,
                        },
                      });
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
              </div>
              {!credentialsAvailable && (
                <p className="mt-3 text-sm text-muted-foreground">
                  Authenticated imports are not configured on this DevFeed server. Public APIs
                  remain available.
                </p>
              )}
              {configured && auth !== "none" && (
                <div className="mt-4 space-y-3">
                  <p className="text-sm text-muted-foreground">
                    A credential is configured. Leaving it unchanged keeps the existing credential.
                    Changing the API origin or authentication requires a replacement.
                  </p>
                  <label className="flex items-center gap-2 text-sm">
                    <Checkbox
                      checked={replaceCredential}
                      onCheckedChange={(checked) => {
                        setReplaceCredential(checked === true);
                        setCredential("");
                      }}
                      disabled={saving || !credentialsAvailable}
                    />
                    Replace credential
                  </label>
                </div>
              )}
              {configured && initialAuth !== "none" && auth === "none" && (
                <p role="status" className="mt-4 text-sm text-muted-foreground">
                  Saving without authentication removes the stored credential.
                </p>
              )}
              {needsCredential && (
                <div className="mt-4 max-w-xl">
                  <Field
                    label={auth === "bearer" ? "Bearer token" : "API key"}
                    hint="Stored encrypted. DevFeed never returns this value to the portal."
                  >
                    <Input
                      type="password"
                      value={credential}
                      onChange={(event) => setCredential(event.target.value)}
                      autoComplete="new-password"
                      required
                      maxLength={8192}
                    />
                  </Field>
                </div>
              )}
            </ResourcePanel>
            <ResourcePanel title="Pagination">
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Pagination mode">
                  <Select
                    label="Pagination mode"
                    value={connector.pagination?.mode ?? "none"}
                    required
                    disabled={saving}
                    options={[
                      { value: "none", label: "Single response" },
                      { value: "cursor", label: "Cursor" },
                      { value: "page", label: "Page number" },
                      { value: "offset", label: "Offset" },
                      { value: "next_url", label: "Next-page URL" },
                    ]}
                    onChange={(mode) => {
                      if (
                        mode !== "none" &&
                        mode !== "cursor" &&
                        mode !== "page" &&
                        mode !== "offset" &&
                        mode !== "next_url"
                      )
                        return;
                      setConnector({
                        ...connector,
                        pagination: {
                          ...connector.pagination,
                          mode,
                          parameter:
                            mode === "none" || mode === "next_url"
                              ? null
                              : (connector.pagination?.parameter ??
                                (mode === "cursor"
                                  ? "cursor"
                                  : mode === "page"
                                    ? "page"
                                    : "offset")),
                        },
                      });
                    }}
                  />
                </Field>
                {connector.pagination?.mode && connector.pagination.mode !== "none" && (
                  <>
                    {connector.pagination.mode !== "next_url" && (
                      <Field label="Pagination parameter">
                        <Input
                          value={connector.pagination.parameter ?? ""}
                          onChange={(event) =>
                            setConnector({
                              ...connector,
                              pagination: {
                                ...connector.pagination,
                                parameter: event.target.value,
                              },
                            })
                          }
                          required
                        />
                      </Field>
                    )}
                    {(connector.pagination.mode === "cursor" ||
                      connector.pagination.mode === "next_url") && (
                      <Field label="Next cursor or URL path">
                        <Input
                          value={connector.pagination.next_path ?? "nextCursor"}
                          onChange={(event) =>
                            setConnector({
                              ...connector,
                              pagination: {
                                ...connector.pagination,
                                next_path: event.target.value,
                              },
                            })
                          }
                          required
                        />
                      </Field>
                    )}
                    <Field
                      label="Page size parameter"
                      hint="Optional query parameter, for example limit."
                    >
                      <Input
                        value={connector.pagination.size_parameter ?? ""}
                        onChange={(event) =>
                          setConnector({
                            ...connector,
                            pagination: {
                              ...connector.pagination,
                              size_parameter: event.target.value || null,
                            },
                          })
                        }
                      />
                    </Field>
                    <Field label="Page size">
                      <Input
                        type="number"
                        value={connector.pagination.page_size ?? 10}
                        onChange={(event) =>
                          setConnector({
                            ...connector,
                            pagination: {
                              ...connector.pagination,
                              page_size: Number(event.target.value),
                            },
                          })
                        }
                        required
                        min={1}
                        max={100}
                        step={1}
                      />
                    </Field>
                  </>
                )}
              </div>
            </ResourcePanel>
          </div>
        ) : (
          <ResourcePanel title="Managed integration">
            <p className="text-sm text-muted-foreground">
              The API configuration and credentials for this integration are managed by DevFeed. You
              can change its name and schedule, pause imports, or sync it now.
            </p>
          </ResourcePanel>
        )}
      </fieldset>
      <div className="flex flex-wrap gap-3 border-t pt-4">
        <Button type="submit" loading={saving}>
          {connection ? "Save changes" : "Create connection"}
        </Button>
        <Button variant="outline" asChild disabled={saving}>
          <Link href={cancelHref} prefetch={false}>
            Cancel
          </Link>
        </Button>
      </div>
    </form>
  );
}
