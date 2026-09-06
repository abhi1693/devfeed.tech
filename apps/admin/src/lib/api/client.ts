/** Orval transport: browser requests only use the same-origin Next.js API gateway. */
export class ApiError extends Error {
  constructor(public status: number, message?: string, public fields: Record<string, string> = {}) {
    super(message ?? (status === 401 ? "Your session has expired. Sign in again." :
      status === 403 ? "You do not have access to this action." :
      "The admin service is unavailable. Try again."));
  }
}

export function returnToLogin(signedOut = false) {
  // A full navigation clears the in-memory Next router and any private page state.
  window.location.replace(new URL(signedOut ? "/login?signed_out=1" : "/login", window.location.origin).href);
}

export async function adminFetch<T>(url: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${url}`, {
    ...options, credentials: "same-origin", cache: "no-store", redirect: "error",
  });
  if (!response.ok) {
    if ([404, 409, 422].includes(response.status)) {
      const body = await response.json().catch(() => null);
      if (typeof body?.detail === "string") throw new ApiError(response.status, body.detail);
      if (Array.isArray(body?.detail)) {
        const fields: Record<string, string> = {};
        for (const issue of body.detail) {
          if (Array.isArray(issue.loc) && typeof issue.msg === "string") fields[issue.loc.slice(1).join(".")] = issue.msg;
        }
        throw new ApiError(response.status, "Please correct the highlighted fields.", fields);
      }
    }
    throw new ApiError(response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
