"use client";

import { useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";
import { parseAccount, type PartnerPreferencesInput } from "./contracts";
import type { Account } from "./types";
import {
  createPartnerRequestScope,
  isPartnerRequestCancelled,
  usePartnerRequest,
} from "./partner-request";

export function usePartnerPreferenceSave({
  accountId,
  csrfToken,
  onSaved,
  onUnavailable,
  successMessage,
  errorMessage,
  confirmationMessage,
}: {
  accountId: string;
  csrfToken: string;
  onSaved: (account: Account) => void;
  onUnavailable: () => void;
  successMessage: string;
  errorMessage: string;
  confirmationMessage: string;
}) {
  const request = usePartnerRequest({ accountId, onUnavailable });
  const scope = useMemo(
    () => ({ accountId, csrfToken, ...createPartnerRequestScope() }),
    [accountId, csrfToken],
  );
  const saved = useRef(onSaved);
  useLayoutEffect(() => {
    saved.current = onSaved;
  }, [onSaved]);
  useLayoutEffect(() => {
    scope.activate();
    return () => scope.cancel();
  }, [scope]);
  const [feedback, setFeedback] = useState({ scope, saving: false, error: "", message: "" });
  const current = feedback.scope === scope ? feedback : { saving: false, error: "", message: "" };

  const save = useCallback(
    async (payload: PartnerPreferencesInput) => {
      // The synchronous guard also rejects duplicate submits before React commits disabled controls.
      const controller = scope.begin(true);
      if (!controller) return;
      setFeedback({ scope, saving: true, error: "", message: "" });
      try {
        const account = await request("preferences", {
          method: "PUT",
          headers: { "content-type": "application/json", "x-csrf-token": scope.csrfToken },
          body: JSON.stringify(payload),
          signal: controller.signal,
          parse: (value) => parseAccount(value, scope.accountId),
          errorMessage,
          invalidMessage: confirmationMessage,
        });
        if (!scope.current(controller)) return;
        saved.current(account);
        if (scope.current(controller))
          setFeedback({ scope, saving: false, error: "", message: successMessage });
      } catch (reason) {
        if (scope.current(controller) && !isPartnerRequestCancelled(reason))
          setFeedback({
            scope,
            saving: false,
            error: reason instanceof Error ? reason.message : errorMessage,
            message: "",
          });
      } finally {
        if (scope.current(controller))
          setFeedback((previous) =>
            previous.scope === scope ? { ...previous, saving: false } : previous,
          );
        scope.finish(controller);
      }
    },
    [scope, request, errorMessage, confirmationMessage, successMessage],
  );
  const clearMessage = useCallback(() => {
    setFeedback((previous) => (previous.scope === scope ? { ...previous, message: "" } : previous));
  }, [scope]);
  return {
    request,
    save,
    saving: current.saving,
    error: current.error,
    message: current.message,
    clearMessage,
  };
}
