"use client";

import { useState, type ComponentProps } from "react";
import { Input } from "@/components/atoms/input";
import { Button } from "@/components/atoms/button";
import { Field } from "@/components/molecules/field";
import { Select } from "@/components/molecules/select";
import type { ResponseMapping } from "@/lib/partner-response-mappings";
import { connectorDefaults } from "@/lib/partner-connectors";
import type {
  ConnectorConfig,
  ConnectorPaginationMode,
  ConnectorAuthMode,
} from "@/lib/api/generated/models";

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
  "Description paths": "fields.description",
  "Pricing paths": "fields.pricing",
  "Platform hosts": "platform_hosts",
  Attribution: "attribution",
  "Page size parameter": "pagination.size_parameter",
  "Pagination parameter": "pagination.parameter",
  "Next token path": "pagination.next_path",
  "Total count path": "pagination.total_path",
  "Has-more path": "pagination.has_more_path",
  "Secret reference": "auth.secret_ref",
  "API key header": "auth.header",
};

export function PartnerConnectorFields({
  value,
  onChange,
  disabled,
  errors = {},
  mappings = [],
}: {
  mappings?: ResponseMapping[];
  errors?: Record<string, string>;
  value: ConnectorConfig;
  onChange: (value: ConnectorConfig) => void;
  disabled: boolean;
}) {
  function fieldError(path: string) {
    return (
      Object.entries(errors)
        .filter(([key]) => key === `connector.${path}` || key.startsWith(`connector.${path}.`))
        .map(([, message]) => message)
        .join(" ") || undefined
    );
  }
  const defaults = connectorDefaults();
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
          <ConnectorTextInput
            {...control}
            className={matched ? mapping?.style : undefined}
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
            max={max}
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
      className={`space-y-6 border-t pt-6 ${errors.connector ? "rounded-lg border border-destructive p-4" : ""}`}
    >
      <div className="grid gap-6 sm:grid-cols-2">
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
      <details open className="space-y-6">
        <summary className="cursor-pointer text-sm font-medium">Product fields</summary>
        <div className="grid gap-6 sm:grid-cols-2">
          {(
            [
              ["external_id", "Product ID paths"],
              ["name", "Product name paths"],
              ["product_url", "Website URL paths"],
              ["listing_url", "Listing URL paths"],
              ["description", "Description paths"],
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
      <details open className="space-y-6">
        <summary className="cursor-pointer text-sm font-medium">Pagination</summary>
        <div className="grid gap-6 sm:grid-cols-2">
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
                      parameter: mode === "page" ? "page" : mode === "offset" ? "offset" : "cursor",
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
          {text("Page size parameter", pagination.size_parameter ?? "", (size_parameter) =>
            onChange({ ...value, pagination: { ...pagination, size_parameter } }),
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
            text("Pagination parameter", pagination.parameter ?? "", (parameter) =>
              onChange({ ...value, pagination: { ...pagination, parameter } }),
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
      <details className="space-y-6">
        <summary className="cursor-pointer text-sm font-medium">Authentication and limits</summary>
        <div className="grid gap-6 sm:grid-cols-2">
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
          {(auth.mode !== "none" || fieldError("auth.secret_ref")) &&
            text(
              "Secret reference",
              auth.secret_ref ?? "",
              (secret_ref) => onChange({ ...value, auth: { ...auth, secret_ref } }),
              "Environment variable on admin-api and worker, e.g. DEVFEED_PARTNER_SECRET_SHIPYARD. Enter the variable name, never the token.",
              true,
            )}
          {(auth.mode === "api_key" || fieldError("auth.header")) &&
            text(
              "API key header",
              auth.header ?? "",
              (header) => onChange({ ...value, auth: { ...auth, header } }),
              undefined,
              true,
            )}
          {number("Requests per minute", "requests_per_minute", 1, 600)}
          {number("Timeout (seconds)", "timeout_seconds", 1, 30)}
          {number("Maximum pages", "max_pages", 1, 1000)}
          {number("Maximum response (bytes)", "max_response_bytes", 1024, 5000000)}
        </div>
      </details>
      <details className="space-y-6">
        <summary className="cursor-pointer text-sm font-medium">
          Filters and query parameters
        </summary>
        {(value.filters ?? []).map((filter, index) => (
          <div key={index} className="grid items-end gap-6 sm:grid-cols-2">
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
                <input
                  {...control}
                  type="checkbox"
                  className="size-4 accent-primary"
                  checked={filter.include_missing ?? true}
                  onChange={(event) =>
                    onChange({
                      ...value,
                      filters: value.filters!.map((item, i) =>
                        i === index ? { ...item, include_missing: event.target.checked } : item,
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
          <div key={index} className="grid items-end gap-6 sm:grid-cols-2">
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
