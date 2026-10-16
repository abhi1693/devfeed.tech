"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ConnectorResponse } from "@devfeed/ui/connector-response";
import type { ResponseMapping } from "@devfeed/ui/connector-response-mappings";
import type { ConnectorConfig, PartnerConnectionOut } from "@/lib/api/generated/models";
import {
  PartnerConnectionResponseResponse,
  PartnerConnectorResponseResponse,
} from "@/lib/api/generated/validators";
import { isPartnerRequestCancelled, usePartnerRequest } from "@/lib/partner-request";

type ResponseSample = { data: unknown; sampled: boolean };

export function PartnerApiResponse({
  accountId,
  connection,
  connector,
  credential,
  csrfToken,
  onUnavailable,
  mappings,
  onResponse,
  disabled = false,
  credentialReady,
}: {
  accountId?: string;
  connection?: PartnerConnectionOut;
  connector: ConnectorConfig;
  credential?: string;
  csrfToken: string;
  onUnavailable: () => void;
  mappings: ResponseMapping[];
  onResponse: (data: unknown) => void;
  disabled?: boolean;
  credentialReady: boolean;
}) {
  const request = usePartnerRequest({ accountId, onUnavailable });
  const [refresh, setRefresh] = useState(0);
  const [state, setState] = useState<{
    identity: object;
    loading?: boolean;
    response?: ResponseSample;
    error?: string;
  }>();
  const completed = useRef<{ identity: object; refresh: number } | undefined>(undefined);
  // Response paths, field mappings and filters only interpret the sample. They do not
  // change the first-page request and must not consume another API preview allowance.
  const requestKey = JSON.stringify({
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
  });
  const provider = connection?.provider;
  const revision = connection?.revision;
  const validEndpoint = (() => {
    try {
      const url = new URL(connector.base_url);
      return (
        url.protocol === "https:" &&
        url.pathname === "/" &&
        !url.search &&
        !url.hash &&
        !url.username &&
        !url.password &&
        Boolean(connector.list_path?.startsWith("/"))
      );
    } catch {
      return false;
    }
  })();
  const ready = Boolean(accountId && validEndpoint && credentialReady);
  // Credentials remain write-only request values, never serialized into a cache key.
  const identity = useMemo(
    () => ({ accountId, provider, revision, requestKey, credential, csrfToken, ready }),
    [accountId, provider, revision, requestKey, credential, csrfToken, ready],
  );
  const current = state?.identity === identity ? state : undefined;

  useEffect(() => {
    if (
      !ready ||
      disabled ||
      (completed.current?.identity === identity && completed.current.refresh === refresh)
    )
      return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setState({ identity, loading: true });
      void request(
        provider
          ? `connections/${encodeURIComponent(provider)}/connector-response`
          : "connections/connector-response",
        {
          method: "POST",
          headers: { "content-type": "application/json", "x-csrf-token": csrfToken },
          signal: controller.signal,
          body: JSON.stringify({
            connector: JSON.parse(requestKey),
            ...(credential ? { credential } : {}),
            ...(provider ? { expected_revision: revision } : {}),
          }),
          parse: (value) => {
            if (!value || typeof value !== "object" || !Object.hasOwn(value, "data"))
              throw new Error("Missing API response data.");
            return provider
              ? PartnerConnectionResponseResponse.parse(value)
              : PartnerConnectorResponseResponse.parse(value);
          },
          errorMessage: "Could not load the API response. Try again shortly.",
          invalidMessage: "Could not confirm the API response. Try again shortly.",
          unavailableOnNotFound: false,
          statusMessages: {
            403: "You do not have permission to preview this connection.",
            404: "This connection is no longer available. Refresh the page.",
            409: "This connection changed. Refresh the page before previewing again.",
            422: "Could not read the API response. Check the endpoint and authentication.",
            429: "Too many previews. Wait a minute before refreshing the response.",
          },
        },
      ).then(
        (response) => {
          if (!controller.signal.aborted) {
            completed.current = { identity, refresh };
            setState({ identity, response });
          }
        },
        (reason: unknown) => {
          if (!controller.signal.aborted && !isPartnerRequestCancelled(reason))
            setState({
              identity,
              error:
                reason instanceof Error
                  ? reason.message
                  : "Could not load the API response. Try again shortly.",
            });
        },
      );
    }, 700);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [
    ready,
    disabled,
    identity,
    refresh,
    request,
    provider,
    revision,
    requestKey,
    credential,
    csrfToken,
  ]);

  useEffect(() => {
    onResponse(current?.response?.data);
  }, [current?.response?.data, onResponse]);

  return (
    <ConnectorResponse
      ready={ready}
      loading={ready && !disabled && (!current || current.loading)}
      refreshing={disabled}
      data={current?.response?.data}
      sampled={current?.response?.sampled}
      mappings={mappings}
      onRefresh={() => setRefresh((value) => value + 1)}
      status={
        !accountId ? (
          <p className="text-sm text-muted-foreground">
            Choose a partner account to preview its product API.
          </p>
        ) : !credentialReady ? (
          <p className="text-sm text-muted-foreground">
            Enter a credential for this API and authentication method to preview the response.
          </p>
        ) : current?.error ? (
          <p role="alert" className="notice">
            {current.error}
          </p>
        ) : undefined
      }
    />
  );
}
