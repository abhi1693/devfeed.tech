"use client";

import { useEffect, useState } from "react";
import type { ResponseMapping } from "@devfeed/ui/connector-response-mappings";
import { ConnectorResponse } from "@devfeed/ui/connector-response";
import { ApiError } from "@/lib/api/client";
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
    <ConnectorResponse
      ready={ready}
      loading={ready && (!current || current.loading)}
      data={current?.response?.data}
      sampled={current?.response?.sampled}
      mappings={mappings}
      onRefresh={() => setRefresh((value) => value + 1)}
      status={
        <>
          <RequestState
            error={
              current?.error instanceof ApiError && Object.keys(current.error.fields).length
                ? undefined
                : current?.error
            }
          />
          <ValidationErrors error={current?.error} />
          {!ready && (
            <p className="text-sm text-muted-foreground">
              Enter the API base URL and products endpoint to preview the response.
            </p>
          )}
        </>
      }
    />
  );
}
