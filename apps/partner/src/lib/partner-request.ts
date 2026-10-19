"use client";

import { useCallback, useLayoutEffect, useMemo, useRef } from "react";
import { useRouter } from "next/navigation";

export function createPartnerRequestScope() {
  let active = false;
  const pending = new Set<AbortController>();
  return {
    activate() {
      active = true;
    },
    cancel() {
      active = false;
      for (const controller of pending) controller.abort();
      pending.clear();
    },
    begin(singleFlight = false) {
      if (!active || (singleFlight && pending.size > 0)) return null;
      const controller = new AbortController();
      pending.add(controller);
      return controller;
    },
    current(controller: AbortController) {
      return active && !controller.signal.aborted;
    },
    finish(controller: AbortController) {
      pending.delete(controller);
    },
  };
}

export function isPartnerRequestCancelled(reason: unknown) {
  return reason instanceof DOMException && reason.name === "AbortError";
}

function cancelled() {
  return new DOMException("Request cancelled.", "AbortError");
}

type PartnerRequestOptions<T> = Omit<RequestInit, "signal"> & {
  signal?: AbortSignal;
  parse: (value: unknown) => T;
  errorMessage: string;
  invalidMessage?: string;
  unavailableOnNotFound?: boolean;
  statusMessages?: Partial<Record<number, string>>;
};

export function usePartnerRequest({
  accountId,
  onUnavailable,
}: {
  accountId?: string;
  onUnavailable: () => void;
}) {
  const router = useRouter();
  const access = useRef({ router, onUnavailable });
  const scope = useMemo(() => ({ accountId, ...createPartnerRequestScope() }), [accountId]);
  useLayoutEffect(() => {
    access.current = { router, onUnavailable };
  }, [router, onUnavailable]);
  useLayoutEffect(() => {
    scope.activate();
    return () => scope.cancel();
  }, [scope]);

  return useCallback(
    async <T>(endpoint: string, options: PartnerRequestOptions<T>): Promise<T> => {
      const {
        parse,
        errorMessage,
        invalidMessage = errorMessage,
        unavailableOnNotFound = Boolean(scope.accountId),
        statusMessages,
        signal,
        ...init
      } = options;
      const controller = scope.begin();
      if (!controller) throw cancelled();
      const abort = () => controller.abort();
      if (signal?.aborted) abort();
      else signal?.addEventListener("abort", abort, { once: true });
      function checkCurrent() {
        if (!scope.current(controller!)) throw cancelled();
      }
      try {
        checkCurrent();
        let response: Response;
        try {
          const prefix = scope.accountId ? `accounts/${encodeURIComponent(scope.accountId)}/` : "";
          response = await fetch(`/api/v1/partner/${prefix}${endpoint}`, {
            credentials: "same-origin",
            cache: "no-store",
            ...init,
            signal: controller.signal,
          });
        } catch {
          checkCurrent();
          throw new Error(errorMessage);
        }
        checkCurrent();
        const write = !["GET", "HEAD"].includes(init.method?.toUpperCase() ?? "GET");
        if (
          response.status === 401 ||
          (!write && response.status === 403) ||
          (response.status === 404 && unavailableOnNotFound)
        ) {
          scope.cancel();
          if (response.status !== 404) access.current.router.replace("/login");
          access.current.onUnavailable();
          throw cancelled();
        }
        let validationMessage: string | undefined;
        if (response.status === 422) {
          try {
            const body: unknown = await response.json();
            if (body && typeof body === "object" && "detail" in body) {
              const detail = body.detail;
              const messages =
                typeof detail === "string"
                  ? [detail]
                  : Array.isArray(detail)
                    ? detail
                        .slice(0, 3)
                        .map((issue: unknown) =>
                          issue && typeof issue === "object" && "msg" in issue ? issue.msg : null,
                        )
                    : [];
              if (
                messages.length &&
                messages.every((message) => typeof message === "string" && message.length <= 500)
              )
                validationMessage = messages
                  .map((message) => String(message).replace(/^Value error, /, ""))
                  .join(" ");
            }
          } catch {
            /* Use the request-specific fallback for unreadable errors. */
          }
          checkCurrent();
        }
        if (!response.ok)
          throw new Error(
            statusMessages?.[response.status] ??
              validationMessage ??
              (write && response.status === 403
                ? "Could not update this account. Refresh the page and try again."
                : errorMessage),
          );
        let value: unknown;
        try {
          value = response.status === 204 ? null : await response.json();
        } catch {
          checkCurrent();
          throw new Error(invalidMessage);
        }
        checkCurrent();
        let parsed: T;
        try {
          parsed = parse(value);
        } catch {
          checkCurrent();
          throw new Error(invalidMessage);
        }
        checkCurrent();
        return parsed;
      } finally {
        signal?.removeEventListener("abort", abort);
        scope.finish(controller);
      }
    },
    [scope],
  );
}
