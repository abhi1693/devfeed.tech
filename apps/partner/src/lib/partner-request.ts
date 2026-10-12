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
};

export function usePartnerAccountRequest({
  accountId,
  onUnavailable,
}: {
  accountId: string;
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
      const { parse, errorMessage, invalidMessage = errorMessage, signal, ...init } = options;
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
          response = await fetch(`/api/v1/partner/accounts/${scope.accountId}/${endpoint}`, {
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
        const write = init.method?.toUpperCase() === "PUT";
        if (
          response.status === 401 ||
          (!write && response.status === 403) ||
          response.status === 404
        ) {
          scope.cancel();
          if (response.status !== 404) access.current.router.replace("/login");
          access.current.onUnavailable();
          throw cancelled();
        }
        if (!response.ok)
          throw new Error(
            write && response.status === 403
              ? "Could not update this account. Refresh the page and try again."
              : errorMessage,
          );
        let value: unknown;
        try {
          value = await response.json();
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
