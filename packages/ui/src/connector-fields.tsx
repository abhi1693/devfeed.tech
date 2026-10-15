"use client";

import { useId, useState, type ComponentProps, type ReactNode } from "react";
import { Check, CircleHelp } from "lucide-react";
import { cn } from "cn";
import { Input } from "./input";
import { Button } from "./button";
import { Checkbox } from "./choice-controls";
import { InfoTip } from "./info-tip";
import { Select } from "./select";
import type { ResponseMapping } from "./connector-response-mappings";
import type {
  ConnectorDefinition,
  ConnectorFieldProps,
  ConnectorFieldRenderer,
  ConnectorPaginationMode,
  ConnectorAuthMode,
} from "./connector-definition";

export type { ConnectorDefinition, ConnectorFieldProps } from "./connector-definition";

function ConnectorField({
  label,
  required = false,
  disabled = false,
  tooltip,
  error,
  children,
}: ConnectorFieldProps) {
  const id = useId();
  const describedBy = [tooltip && `${id}-help`, error && `${id}-error`].filter(Boolean).join(" ");
  return (
    <div className="min-w-0 space-y-2" data-invalid={!!error} data-disabled={disabled}>
      <div className="flex items-center gap-1.5">
        <label htmlFor={id} className="text-sm font-medium">
          {label}
          {required && <span aria-hidden="true"> *</span>}
        </label>
        {tooltip && <InfoTip label={`About ${label}`}>{tooltip}</InfoTip>}
      </div>
      {tooltip && (
        <span id={`${id}-help`} className="sr-only">
          {tooltip}
        </span>
      )}
      {children({
        id,
        required,
        disabled,
        "aria-invalid": !!error,
        "aria-describedby": describedBy || undefined,
      })}
      {error && (
        <p id={`${id}-error`} className="text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

const paths = (value: string) =>
  value
    .split(",")
    .map((path) => path.trim())
    .filter(Boolean);

// Keep delimiter typing intact while reflecting mode changes and removed rows.
function ConnectorTextInput({
  value,
  onChange,
  ...props
}: Omit<ComponentProps<typeof Input>, "value" | "onChange"> & {
  value: string;
  onChange: (value: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  return (
    <Input
      {...props}
      value={draft === value || paths(draft).join(", ") === value ? draft : value}
      onChange={(event) => {
        setDraft(event.target.value);
        onChange(event.target.value);
      }}
    />
  );
}

const fieldPaths: Record<string, string> = {
  "API base URL": "base_url",
  "Products endpoint": "list_path",
  "Product detail endpoint": "detail_path",
  "Product list paths": "items_paths",
  "Detail response root": "detail_root",
  "Listing URL template": "listing_url_template",
  "Product ID paths": "fields.external_id",
  "Product name paths": "fields.name",
  "Website URL paths": "fields.product_url",
  "Listing URL paths": "fields.listing_url",
  "Tagline paths": "fields.tagline",
  "Pricing paths": "fields.pricing",
  "Platform hosts": "platform_hosts",
  Attribution: "attribution",
  "Page size parameter": "pagination.size_parameter",
  "Pagination parameter": "pagination.parameter",
  "Next token path": "pagination.next_path",
  "Total count path": "pagination.total_path",
  "Has-more path": "pagination.has_more_path",
  "API key header": "auth.header",
};

export function ConnectorCredentialFields({
  auth,
  onAuthChange,
  credential,
  onCredentialChange,
  configured,
  needsCredential,
  replaceCredential,
  onReplaceCredentialChange,
  credentialsAvailable,
  disabled,
  errors = {},
  Field = ConnectorField,
}: {
  auth: NonNullable<ConnectorDefinition["auth"]>;
  onAuthChange: (auth: NonNullable<ConnectorDefinition["auth"]>) => void;
  credential: string;
  onCredentialChange: (value: string) => void;
  configured: boolean;
  needsCredential: boolean;
  replaceCredential: boolean;
  onReplaceCredentialChange: (replace: boolean) => void;
  credentialsAvailable: boolean;
  disabled: boolean;
  errors?: Record<string, string>;
  Field?: ConnectorFieldRenderer;
}) {
  const mode = auth.mode ?? "none";
  return (
    <div className="space-y-3">
      <Field
        label="Authentication method"
        required
        disabled={disabled}
        error={errors["connector.auth.mode"] ?? errors["connector.auth"]}
      >
        {(control) => (
          <Select
            {...control}
            label="Authentication method"
            value={mode}
            options={[
              { value: "none", label: "No authentication" },
              {
                value: "bearer",
                label: "Bearer token",
                disabled: !credentialsAvailable && mode !== "bearer",
              },
              {
                value: "api_key",
                label: "API key",
                disabled: !credentialsAvailable && mode !== "api_key",
              },
            ]}
            onChange={(next) => {
              if (next !== "none" && next !== "bearer" && next !== "api_key") return;
              onAuthChange({ ...auth, mode: next });
              onCredentialChange("");
              onReplaceCredentialChange(false);
            }}
          />
        )}
      </Field>
      {mode === "api_key" && (
        <Field
          label="API key header"
          required
          disabled={disabled}
          error={errors["connector.auth.header"]}
        >
          {(control) => (
            <Input
              {...control}
              value={auth.header ?? "X-API-Key"}
              maxLength={100}
              onChange={(event) => onAuthChange({ ...auth, header: event.target.value })}
            />
          )}
        </Field>
      )}
      {!credentialsAvailable && (
        <p className="text-sm text-muted-foreground">
          Authenticated imports are not configured on this DevFeed server. Public APIs remain
          available.
        </p>
      )}
      {configured && mode !== "none" && (
        <>
          <p className="text-sm text-muted-foreground">
            A credential is configured. Leaving it unchanged keeps the existing credential. Changing
            the API origin or authentication requires a replacement.
          </p>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={replaceCredential}
              disabled={disabled || !credentialsAvailable}
              onCheckedChange={(checked) => {
                onReplaceCredentialChange(checked === true);
                onCredentialChange("");
              }}
            />
            Replace credential
          </label>
        </>
      )}
      {configured && mode === "none" && (
        <p role="status" className="text-sm text-muted-foreground">
          Saving without authentication removes the stored credential.
        </p>
      )}
      {needsCredential && (
        <Field
          label={mode === "bearer" ? "Bearer token" : "API key"}
          required
          disabled={disabled || !credentialsAvailable}
          tooltip="Stored encrypted. DevFeed never returns this value to the portal."
          error={errors.credential}
        >
          {(control) => (
            <Input
              {...control}
              type="password"
              value={credential}
              onChange={(event) => onCredentialChange(event.target.value)}
              autoComplete="new-password"
              maxLength={8192}
            />
          )}
        </Field>
      )}
    </div>
  );
}

export function ConnectorFields<C extends ConnectorDefinition>({
  value,
  onChange,
  defaults,
  disabled,
  errors = {},
  mappings = [],
  authentication,
  suggestPaginationParameters = false,
  limits = {},
  Field = ConnectorField,
}: {
  mappings?: ResponseMapping[];
  errors?: Record<string, string>;
  value: C;
  onChange: (value: C) => void;
  defaults: ConnectorDefinition;
  disabled: boolean;
  /** Supply application-specific credential state and controls. */
  authentication?: ReactNode;
  suggestPaginationParameters?: boolean;
  limits?: Partial<
    Record<"requests_per_minute" | "timeout_seconds" | "max_pages" | "max_response_bytes", number>
  >;
  Field?: ConnectorFieldRenderer;
}) {
  function fieldError(path: string) {
    return (
      Object.entries(errors)
        .filter(([key]) => key === `connector.${path}` || key.startsWith(`connector.${path}.`))
        .map(([, message]) => message)
        .join(" ") || undefined
    );
  }
  const fields = { ...defaults.fields!, ...value.fields };
  const pagination = { ...defaults.pagination!, ...value.pagination };
  const auth = { ...defaults.auth!, ...value.auth };
  function text(
    label: string,
    current: string,
    change: (value: string) => void,
    tooltip?: string,
    required = false,
  ) {
    const mapping = mappings.find((item) => item.field === fieldPaths[label]);
    const matched = !!mapping?.addresses.length;
    return (
      <Field
        key={label}
        label={label}
        disabled={disabled}
        required={required}
        tooltip={tooltip}
        error={fieldError(
          fieldPaths[label] ??
            (label.startsWith("Filter ")
              ? `filters.${Number(label.split(" ")[1]) - 1}.${label.endsWith("path") ? "path" : "values"}`
              : label.startsWith("Parameter ")
                ? `parameters.${Object.keys(value.parameters ?? {})[Number(label.split(" ")[1]) - 1]}`
                : ""),
        )}
      >
        {(control) => (
          <div className="relative">
            <ConnectorTextInput
              {...control}
              aria-describedby={
                [control["aria-describedby"], mapping && `${control.id}-mapping`]
                  .filter(Boolean)
                  .join(" ") || undefined
              }
              className={cn(matched && mapping?.style, mapping && "pr-9")}
              title={
                mapping
                  ? matched
                    ? `${mapping.label}: found in sample`
                    : "Not found in this sample"
                  : undefined
              }
              value={current}
              onChange={change}
            />
            {mapping && (
              <span
                id={`${control.id}-mapping`}
                role="img"
                data-mapping-status={matched ? "matched" : "unmatched"}
                aria-label={`${mapping.label} ${matched ? "found in sample" : "not found in this sample"}`}
                className={cn(
                  "pointer-events-none absolute inset-y-0 right-3 flex items-center",
                  matched ? mapping.style : "text-muted-foreground",
                  "bg-transparent",
                )}
              >
                {matched ? (
                  <Check aria-hidden="true" className="size-3.5 shrink-0" />
                ) : (
                  <CircleHelp aria-hidden="true" className="size-3.5 shrink-0" />
                )}
                <span className="sr-only">
                  {mapping.label}: {matched ? "found in sample" : "not found in this sample"}
                </span>
              </span>
            )}
          </div>
        )}
      </Field>
    );
  }
  function number(
    label: string,
    key: "requests_per_minute" | "timeout_seconds" | "max_pages" | "max_response_bytes",
    min: number,
    max: number,
  ) {
    return (
      <Field key={key} label={label} disabled={disabled} required error={fieldError(key)}>
        {(control) => (
          <Input
            {...control}
            type="number"
            min={min}
            max={limits[key] ?? max}
            step={1}
            value={value[key] ?? defaults[key]}
            onChange={(event) => onChange({ ...value, [key]: Number(event.target.value) })}
          />
        )}
      </Field>
    );
  }
  return (
    <div
      className={`space-y-[var(--connector-section-gap,1.5rem)] border-t pt-[var(--connector-section-gap,1.5rem)] ${errors.connector ? "rounded-lg border border-destructive p-4" : ""}`}
    >
      <div className="grid gap-[var(--connector-field-gap,1.5rem)] sm:grid-cols-2">
        {text(
          "API base URL",
          value.base_url,
          (base_url) => onChange({ ...value, base_url }),
          "Public HTTPS origin, for example https://api.example.com",
          true,
        )}
        {text(
          "Products endpoint",
          value.list_path ?? "",
          (list_path) => onChange({ ...value, list_path }),
          "Path on the API origin, for example /v1/products",
          true,
        )}
        {text(
          "Product detail endpoint",
          value.detail_path ?? "",
          (detail_path) => onChange({ ...value, detail_path: detail_path || null }),
          "Optional path containing {id}; leave blank when the list includes full product details.",
        )}
        {text(
          "Product list paths",
          value.items_paths?.join(", ") ?? "",
          (items_paths) =>
            onChange({ ...value, items_paths: items_paths ? paths(items_paths) : [""] }),
          "Dot paths tried in order, separated by commas. Leave empty to use a top-level array.",
        )}
        {text(
          "Detail response root",
          value.detail_root ?? "",
          (detail_root) => onChange({ ...value, detail_root }),
          "Optional dot path to the product object, for example data.product",
        )}
        {text(
          "Listing URL template",
          value.listing_url_template ?? "",
          (listing_url_template) =>
            onChange({ ...value, listing_url_template: listing_url_template || null }),
          "Optional public URL containing {id}, used when the record has no listing URL.",
        )}
      </div>
      <details open className="space-y-[var(--connector-section-gap,1.5rem)]">
        <summary className="cursor-pointer text-sm font-medium">Product fields</summary>
        <div className="grid gap-[var(--connector-field-gap,1.5rem)] sm:grid-cols-2">
          {(
            [
              ["external_id", "Product ID paths"],
              ["name", "Product name paths"],
              ["product_url", "Website URL paths"],
              ["listing_url", "Listing URL paths"],
              ["tagline", "Tagline paths"],
              ["pricing", "Pricing paths"],
            ] as const
          ).map(([key, label]) =>
            text(
              label,
              fields[key]?.join(", ") ?? "",
              (input) => onChange({ ...value, fields: { ...fields, [key]: paths(input) } }),
              "Dot paths tried in order, separated by commas.",
            ),
          )}
          {text(
            "Platform hosts",
            value.platform_hosts?.join(", ") ?? "",
            (input) => onChange({ ...value, platform_hosts: paths(input) }),
            "Optional hostnames used to distinguish platform listings from product websites.",
          )}
          {text("Attribution", value.attribution ?? "", (attribution) =>
            onChange({ ...value, attribution }),
          )}
        </div>
      </details>
      <details open className="space-y-[var(--connector-section-gap,1.5rem)]">
        <summary className="cursor-pointer text-sm font-medium">Pagination</summary>
        <div className="grid gap-[var(--connector-field-gap,1.5rem)] sm:grid-cols-2">
          <Field
            label="Pagination mode"
            required
            disabled={disabled}
            error={fieldError("pagination.mode")}
          >
            {(control) => (
              <Select
                {...control}
                label="Pagination mode"
                value={pagination.mode ?? "none"}
                onChange={(mode) =>
                  onChange({
                    ...value,
                    pagination: {
                      ...pagination,
                      mode: mode as ConnectorPaginationMode,
                      ...(suggestPaginationParameters
                        ? {
                            parameter:
                              mode === "none" || mode === "next_url"
                                ? null
                                : (pagination.parameter ??
                                  (mode === "cursor"
                                    ? "cursor"
                                    : mode === "page"
                                      ? "page"
                                      : "offset")),
                          }
                        : {}),
                    },
                  })
                }
                options={[
                  { value: "none", label: "None" },
                  { value: "cursor", label: "Cursor" },
                  { value: "page", label: "Page number" },
                  { value: "offset", label: "Offset" },
                  { value: "next_url", label: "Next-page URL" },
                ]}
              />
            )}
          </Field>
          {text(
            "Page size parameter",
            pagination.size_parameter ?? "",
            (size_parameter) =>
              onChange({
                ...value,
                pagination: { ...pagination, size_parameter: size_parameter || null },
              }),
            "Optional query parameter for page size. Leave blank to send no page-size parameter.",
          )}
          <Field
            label="Page size"
            disabled={disabled}
            required
            error={fieldError("pagination.page_size")}
          >
            {(control) => (
              <Input
                {...control}
                type="number"
                min={1}
                max={100}
                value={pagination.page_size}
                onChange={(event) =>
                  onChange({
                    ...value,
                    pagination: { ...pagination, page_size: Number(event.target.value) },
                  })
                }
              />
            )}
          </Field>
          {pagination.mode !== "none" &&
            pagination.mode !== "next_url" &&
            text(
              "Pagination parameter",
              pagination.parameter ?? "",
              (parameter) =>
                onChange({ ...value, pagination: { ...pagination, parameter: parameter || null } }),
              "Query parameter used for the cursor, page number, or offset.",
              true,
            )}
          {(pagination.mode === "cursor" || pagination.mode === "next_url") &&
            text("Next token path", pagination.next_path ?? "", (next_path) =>
              onChange({ ...value, pagination: { ...pagination, next_path } }),
            )}
          {pagination.mode === "page" && (
            <Field
              label="First page"
              disabled={disabled}
              required
              error={fieldError("pagination.start")}
            >
              {(control) => (
                <Input
                  {...control}
                  type="number"
                  min={0}
                  max={1000000}
                  value={pagination.start}
                  onChange={(event) =>
                    onChange({
                      ...value,
                      pagination: { ...pagination, start: Number(event.target.value) },
                    })
                  }
                />
              )}
            </Field>
          )}
          {(pagination.mode === "page" || pagination.mode === "offset") && (
            <>
              {text("Total count path", pagination.total_path ?? "", (total_path) =>
                onChange({
                  ...value,
                  pagination: { ...pagination, total_path: total_path || null },
                }),
              )}
              {text("Has-more path", pagination.has_more_path ?? "", (has_more_path) =>
                onChange({
                  ...value,
                  pagination: { ...pagination, has_more_path: has_more_path || null },
                }),
              )}
            </>
          )}
        </div>
      </details>
      <details
        open={authentication !== undefined}
        className="space-y-[var(--connector-section-gap,1.5rem)]"
      >
        <summary className="cursor-pointer text-sm font-medium">Authentication and limits</summary>
        <div className="grid gap-[var(--connector-field-gap,1.5rem)] sm:grid-cols-2">
          {authentication === undefined ? (
            <>
              <Field
                label="Authentication"
                required
                disabled={disabled}
                error={fieldError("auth.mode") ?? errors["connector.auth"]}
              >
                {(control) => (
                  <Select
                    {...control}
                    label="Authentication"
                    required
                    value={auth.mode ?? "none"}
                    onChange={(mode) =>
                      onChange({ ...value, auth: { ...auth, mode: mode as ConnectorAuthMode } })
                    }
                    options={[
                      { value: "none", label: "None" },
                      { value: "bearer", label: "Bearer token" },
                      { value: "api_key", label: "API key header" },
                    ]}
                  />
                )}
              </Field>
              {(auth.mode === "api_key" || fieldError("auth.header")) &&
                text(
                  "API key header",
                  auth.header ?? "",
                  (header) => onChange({ ...value, auth: { ...auth, header } }),
                  undefined,
                  true,
                )}
            </>
          ) : (
            authentication
          )}
          {number("Requests per minute", "requests_per_minute", 1, 600)}
          {number("Timeout (seconds)", "timeout_seconds", 1, 30)}
          {number("Maximum pages", "max_pages", 1, 1000)}
          {number("Maximum response (bytes)", "max_response_bytes", 1024, 5000000)}
        </div>
      </details>
      <details className="space-y-[var(--connector-section-gap,1.5rem)]">
        <summary className="cursor-pointer text-sm font-medium">
          Filters and query parameters
        </summary>
        {(value.filters ?? []).map((filter, index) => (
          <div
            key={index}
            className="grid items-end gap-[var(--connector-field-gap,1.5rem)] sm:grid-cols-2"
          >
            {text(`Filter ${index + 1} path`, filter.path, (path) =>
              onChange({
                ...value,
                filters: value.filters!.map((item, i) => (i === index ? { ...item, path } : item)),
              }),
            )}
            {text(`Filter ${index + 1} values`, filter.values.join(", "), (input) =>
              onChange({
                ...value,
                filters: value.filters!.map((item, i) =>
                  i === index ? { ...item, values: paths(input) } : item,
                ),
              }),
            )}
            <Field
              label={`Filter ${index + 1}: include missing values`}
              disabled={disabled}
              error={fieldError(`filters.${index}.include_missing`)}
            >
              {(control) => (
                <Checkbox
                  {...control}
                  checked={filter.include_missing ?? true}
                  onCheckedChange={(checked) =>
                    onChange({
                      ...value,
                      filters: value.filters!.map((item, i) =>
                        i === index ? { ...item, include_missing: checked === true } : item,
                      ),
                    })
                  }
                />
              )}
            </Field>
            <Button
              variant="outline"
              disabled={disabled}
              onClick={() =>
                onChange({ ...value, filters: value.filters!.filter((_, i) => i !== index) })
              }
            >
              Remove filter {index + 1}
            </Button>
          </div>
        ))}
        <Button
          variant="outline"
          disabled={disabled || (value.filters?.length ?? 0) >= 10}
          onClick={() =>
            onChange({
              ...value,
              filters: [
                ...(value.filters ?? []),
                { path: "categories", values: [], include_missing: false },
              ],
            })
          }
        >
          Add filter
        </Button>
        {Object.entries(value.parameters ?? {}).map(([key, parameter], index) => (
          <div
            key={index}
            className="grid items-end gap-[var(--connector-field-gap,1.5rem)] sm:grid-cols-2"
          >
            {text(`Parameter ${index + 1} name`, key, (next) =>
              onChange({
                ...value,
                parameters: Object.fromEntries(
                  Object.entries(value.parameters ?? {}).map(([name, input], i) => [
                    i === index ? next : name,
                    input,
                  ]),
                ),
              }),
            )}
            {text(`Parameter ${index + 1} value`, String(parameter), (input) =>
              onChange({ ...value, parameters: { ...value.parameters, [key]: input } }),
            )}
            <Button
              variant="outline"
              disabled={disabled}
              onClick={() =>
                onChange({
                  ...value,
                  parameters: Object.fromEntries(
                    Object.entries(value.parameters ?? {}).filter(([name]) => name !== key),
                  ),
                })
              }
            >
              Remove parameter {index + 1}
            </Button>
          </div>
        ))}
        <Button
          variant="outline"
          disabled={disabled || Object.keys(value.parameters ?? {}).length >= 20}
          onClick={() => {
            let key = "parameter";
            while (key in (value.parameters ?? {})) key += "_";
            onChange({ ...value, parameters: { ...value.parameters, [key]: "" } });
          }}
        >
          Add query parameter
        </Button>
      </details>
    </div>
  );
}
