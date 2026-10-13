"use client";

import type { ReactNode } from "react";
import { Button } from "./button";
import type { ResponseMapping } from "./connector-response-mappings";

/** Application adapters own fetching, credentials and errors; this panel only renders the sample. */
export function ConnectorResponse({
  ready,
  loading = false,
  refreshing = false,
  data,
  sampled = false,
  mappings = [],
  onRefresh,
  status,
  className = "",
}: {
  ready: boolean;
  loading?: boolean;
  refreshing?: boolean;
  data?: unknown;
  sampled?: boolean;
  mappings?: ResponseMapping[];
  onRefresh: () => void;
  status?: ReactNode;
  className?: string;
}) {
  return (
    <aside
      aria-label="API response preview"
      aria-busy={loading}
      className={`min-w-0 space-y-4 rounded-lg border bg-card p-[var(--resource-panel-padding,1.5rem)] xl:sticky xl:top-24 ${className}`}
    >
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold">API response</h2>
        <Button
          variant="outline"
          size="sm"
          disabled={!ready || loading || refreshing}
          onClick={onRefresh}
        >
          Refresh
        </Button>
      </div>
      {status}
      {!ready && !status && (
        <p className="text-sm text-muted-foreground">
          Enter the API base URL and products endpoint to preview the response.
        </p>
      )}
      {loading && (
        <p role="status" className="text-sm text-muted-foreground">
          Loading API response…
        </p>
      )}
      {data !== undefined && (
        <>
          {sampled && (
            <p className="text-sm text-muted-foreground">
              Sample response; long lists and values are shortened. Unmatched paths may exist in a
              detail response.
            </p>
          )}
          <pre
            role="textbox"
            aria-readonly="true"
            aria-multiline="true"
            tabIndex={0}
            aria-label="API response JSON"
            className="max-h-[70vh] overflow-auto rounded-md border bg-muted p-4 text-xs leading-6"
          >
            {responseLines(data, mappings)}
          </pre>
        </>
      )}
    </aside>
  );
}

function responseLines(data: unknown, mappings: ResponseMapping[]): ReactNode[] {
  const lines: ReactNode[] = [];
  function render(value: unknown, address: string[], prefix = "", comma = false) {
    const matches = mappings.filter((mapping) =>
      mapping.addresses.includes(JSON.stringify(address)),
    );
    const object = value !== null && typeof value === "object";
    const array = Array.isArray(value);
    const entries = object ? Object.entries(value) : [];
    const opening = array ? "[" : "{";
    const closing = array ? "]" : "}";
    const content = object ? (entries.length ? opening : opening + closing) : JSON.stringify(value);
    lines.push(
      <span
        key={JSON.stringify(address)}
        data-mappings={matches.map((item) => item.field).join(" ")}
        title={matches.map((item) => item.label).join(", ")}
        className={`block min-w-max border-l-2 px-2 ${matches.length ? matches[0].style : "border-transparent"}`}
      >
        {"  ".repeat(address.length)}
        {matches.length > 1 &&
          matches.map((item) => (
            <span
              key={item.field}
              aria-label={item.label}
              className={`mr-1 inline-block h-2 w-2 rounded-full border ${item.style}`}
            />
          ))}
        {prefix}
        {content}
        {(!object || !entries.length) && comma ? "," : ""}
        {"\n"}
      </span>,
    );
    if (object && entries.length) {
      entries.forEach(([key, child], index) =>
        render(
          child,
          [...address, key],
          array ? "" : JSON.stringify(key) + ": ",
          index < entries.length - 1,
        ),
      );
      lines.push(
        <span
          key={JSON.stringify(address) + "-end"}
          className="block min-w-max border-l-2 border-transparent px-2"
        >
          {"  ".repeat(address.length)}
          {closing}
          {comma ? "," : ""}
          {"\n"}
        </span>,
      );
    }
  }
  render(data, []);
  return lines;
}
