"use client";

import { useEffect, useState, type ReactNode } from "react";
import type { ResponseMapping } from "@/lib/partner-response-mappings";
import { ApiError } from "@/lib/api/client";
import { Button } from "@/components/atoms/button";
import { RequestState } from "@/components/molecules/request-state";
import { ValidationErrors } from "@/components/molecules/validation-errors";
import { useAdmin } from "@/components/molecules/admin-session";
import { adminPartnerConnectorResponse } from "@/lib/api/generated/admin";
import type { ConnectorConfig, ConnectorResponseOut } from "@/lib/api/generated/models";

export function PartnerApiResponse({
  provider,
  connector,
  mappings,
  onResponse,
}: {
  provider: string;
  connector: ConnectorConfig;
  mappings: ResponseMapping[];
  onResponse: (data: unknown) => void;
}) {
  const admin = useAdmin();
  const [refresh, setRefresh] = useState(0);
  const [state, setState] = useState<{
    key: string;
    loading?: boolean;
    response?: ConnectorResponseOut;
    error?: Error;
  }>();
  // Mapping edits do not refetch the API. Only request settings determine the preview.
  const key = JSON.stringify({
    provider: provider || "connector-preview",
    connector: {
      base_url: connector.base_url,
      list_path: connector.list_path,
      auth: connector.auth,
      parameters: connector.parameters,
      pagination: {
        mode: connector.pagination?.mode,
        parameter: connector.pagination?.parameter,
        size_parameter: connector.pagination?.size_parameter,
        page_size: connector.pagination?.page_size,
        start: connector.pagination?.start,
      },
      timeout_seconds: connector.timeout_seconds,
      max_response_bytes: connector.max_response_bytes,
      requests_per_minute: connector.requests_per_minute,
    },
  });
  const ready =
    /^https:\/\/[^/]+\/?$/.test(connector.base_url) && !!connector.list_path?.startsWith("/");
  useEffect(() => {
    if (!ready) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setState({ key, loading: true });
      void adminPartnerConnectorResponse(JSON.parse(key), {
        signal: controller.signal,
        headers: { "X-CSRF-Token": admin.csrf_token },
      }).then(
        (response) => {
          if (!controller.signal.aborted) setState({ key, response });
        },
        (error: unknown) => {
          if (!controller.signal.aborted)
            setState({
              key,
              error: error instanceof Error ? error : new Error("Could not load API response."),
            });
        },
      );
    }, 700);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [key, ready, refresh, admin.csrf_token]);
  const current = state?.key === key ? state : undefined;
  useEffect(() => {
    onResponse(current?.response?.data);
  }, [current?.response?.data, onResponse]);
  return (
    <aside
      aria-label="API response preview"
      className="min-w-0 space-y-4 rounded-lg border bg-card p-6 xl:sticky xl:top-24"
    >
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold">API response</h2>
        <Button
          variant="outline"
          size="sm"
          disabled={!ready || current?.loading}
          onClick={() => setRefresh((value) => value + 1)}
        >
          Refresh
        </Button>
      </div>
      {!ready ? (
        <p className="text-sm text-muted-foreground">
          Enter the API base URL and products endpoint to preview the response.
        </p>
      ) : (
        <>
          <RequestState
            loading={!current || current.loading}
            error={
              current?.error instanceof ApiError && Object.keys(current.error.fields).length
                ? undefined
                : current?.error
            }
          />
          <ValidationErrors error={current?.error} />
          {current?.response && (
            <>
              {current.response.sampled && (
                <p className="text-sm text-muted-foreground">
                  Sample response; long lists and values are shortened. Unmatched paths may exist in
                  a detail response.
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
                {responseLines(current.response.data, mappings)}
              </pre>
            </>
          )}
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
