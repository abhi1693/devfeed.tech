import "server-only";
import type { GatewayOperation } from "./gateway";
import { readRequestBody, RequestBodyTimeout, RequestBodyTooLarge } from "./request-body";

type TransportOptions<Operation extends GatewayOperation> = {
  apiOrigin: () => string;
  webOrigin: () => string;
  originAllowed?: (origin: string | null) => boolean;
  filterCookies?: (value: string, operation: Operation) => string;
  traceHeaders: () => Record<string, string>;
  unavailableDetail: string;
};

export type GatewayBodyValidator = (
  body: ArrayBuffer,
) => Response | null | Promise<Response | null>;
type ForwardOptions = { validateBody?: GatewayBodyValidator };

const safe = new Set(["GET", "HEAD", "OPTIONS"]);

export function methodNotAllowed(allowedMethods: readonly string[]): Response {
  return Response.json(
    { detail: "Method not allowed" },
    { status: 405, headers: { Allow: allowedMethods.join(", ") } },
  );
}

/** Forward an already selected operation; routing and authorization stay with their owners. */
export class GatewayTransport<Operation extends GatewayOperation = GatewayOperation> {
  constructor(private readonly options: TransportOptions<Operation>) {}

  async forward(
    request: Request,
    operation: Operation,
    path: string,
    options: ForwardOptions = {},
  ): Promise<Response> {
    if (request.method !== operation.method) return methodNotAllowed([operation.method]);
    const { policy } = operation;
    try {
      const origin = this.options.webOrigin();
      const incoming = new URL(request.url);
      // Host/proxy headers do not determine callback redirects or CSRF trust.
      if (!safe.has(operation.method)) {
        const requestOrigin = request.headers.get("origin");
        const originAllowed = this.options.originAllowed
          ? this.options.originAllowed(requestOrigin)
          : requestOrigin === origin;
        if (!originAllowed)
          return Response.json({ detail: "Invalid request origin" }, { status: 403 });
      }
      const headers = new Headers({ Accept: "application/json" });
      for (const name of [
        "cookie",
        "content-type",
        "origin",
        "x-csrf-token",
        "if-none-match",
        "last-event-id",
      ]) {
        let value = request.headers.get(name);
        if (name === "cookie" && value && this.options.filterCookies)
          value = this.options.filterCookies(value, operation);
        if (value) headers.set(name, value);
      }
      let body: ArrayBuffer | undefined;
      if (!safe.has(operation.method)) {
        body = await readRequestBody(request, policy.bodyLimit, policy.bodyTimeoutMs);
        const rejection = await options.validateBody?.(body);
        if (rejection) return rejection;
      }
      request.signal.throwIfAborted();
      for (const [key, value] of Object.entries(this.options.traceHeaders()))
        headers.set(key, value);
      const upstream = await fetch(`${this.options.apiOrigin()}${path}${incoming.search}`, {
        method: operation.method,
        headers,
        body,
        redirect: "manual",
        cache: "no-store",
        signal: AbortSignal.any([request.signal, AbortSignal.timeout(policy.upstreamTimeoutMs)]),
      });
      const resultHeaders = new Headers({
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
      });
      for (const name of [
        "content-type",
        "location",
        "x-request-id",
        "x-devfeed-version",
        "etag",
        "x-accel-buffering",
        "retry-after",
      ]) {
        const value = upstream.headers.get(name);
        if (value) resultHeaders.set(name, value);
      }
      // Callback rotates a session and clears the state cookie; never collapse Set-Cookie.
      for (const value of upstream.headers.getSetCookie())
        resultHeaders.append("Set-Cookie", value);
      return new Response(upstream.body, { status: upstream.status, headers: resultHeaders });
    } catch (error) {
      if (error instanceof RequestBodyTooLarge)
        return Response.json(
          { detail: "Request too large" },
          { status: 413, headers: { "Cache-Control": "no-store" } },
        );
      if (error instanceof RequestBodyTimeout)
        return Response.json(
          { detail: "Incomplete request" },
          { status: 408, headers: { "Cache-Control": "no-store" } },
        );
      // Callback URLs, codes, provider errors, cookies and tokens must not be logged.
      return Response.json(
        { detail: this.options.unavailableDetail },
        { status: 503, headers: { "Cache-Control": "no-store" } },
      );
    }
  }
}
