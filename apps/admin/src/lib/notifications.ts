"use client";

import { toast, type ExternalToast } from "sonner";
import { ApiError, returnToLogin } from "./api/client";

/** Transient feedback only: field validation and persistent page states stay inline. */
export const notify = {
  success: toast.success,
  info: toast.info,
  warning: (message: string, options?: ExternalToast) =>
    toast.warning(message, { duration: 8000, ...options }),
  error: (message: string, options?: ExternalToast) =>
    toast.error(message, { duration: 8000, ...options }),
};

export function notifyFailure(error: unknown, title: string, id?: string) {
  if (typeof error === "object" && error !== null && "name" in error && error.name === "AbortError")
    return;
  if (error instanceof ApiError && error.status === 401) {
    returnToLogin();
    return;
  }
  // Only validated API messages are safe to display. Network/implementation errors
  // may contain internal details; never serialize the request, response, or token.
  notify.error(title, {
    id,
    description:
      error instanceof ApiError
        ? error.message
        : "Please try again. If the problem continues, check the admin service.",
  });
}
