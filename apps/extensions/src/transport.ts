import { GatewayRouter, type ExtensionGatewayOperation } from "@devfeed/http/gateway";
import { readRequestBody } from "@devfeed/http/request-body";
import { extensionGatewayOperations } from "../../web/src/lib/api/generated/extension-operations";
import { publicReadCacheControl } from "../../web/src/lib/public-read-cache";

export const publicOrigin = "https://devfeed.tech";

/** Browser-managed sessions stay with Chrome/Edge; operation policy controls forwarding. */
export class ExtensionReaderTransport {
  private readonly router: GatewayRouter<ExtensionGatewayOperation>;

  constructor(
    private readonly network: typeof fetch,
    operations: readonly ExtensionGatewayOperation[] = extensionGatewayOperations,
  ) {
    this.router = new GatewayRouter({ operations });
  }

  readonly request: typeof fetch = async (input, init) => {
    const original = input instanceof Request ? input : undefined;
    const url = new URL(original ? original.url : String(input), publicOrigin);
    if (url.origin !== publicOrigin || url.username || url.password)
      throw new Error("Unexpected reader API origin");
    const method = (init?.method ?? original?.method ?? "GET").toUpperCase();
    let segments: string[];
    try {
      segments = url.pathname.slice(1).split("/").map(decodeURIComponent);
    } catch {
      return this.unsupported();
    }
    const match = this.router.resolve(method, segments);
    if (match.kind !== "matched") return this.unsupported();
    const { policy } = match.operation;
    const { extension } = policy;
    const supplied = new Headers(init?.headers ?? original?.headers);
    const headers = new Headers({ Accept: "application/json" });
    if (extension.cacheControl !== null)
      headers.set(
        "Cache-Control",
        extension.cacheControl === "public-read" ? publicReadCacheControl : extension.cacheControl,
      );
    for (const name of extension.requestHeaders) {
      const value = supplied.get(name);
      if (value !== null) headers.set(name, value);
    }
    const caller = init?.signal ?? original?.signal;
    const deadline = AbortSignal.timeout(extension.timeoutMs);
    const signal = caller ? AbortSignal.any([caller, deadline]) : deadline;
    signal.throwIfAborted();
    let body: BodyInit | undefined;
    if (method !== "GET" && method !== "HEAD") {
      body =
        init?.body ??
        (original
          ? await readRequestBody(original.clone(), policy.bodyLimit, policy.bodyTimeoutMs, signal)
          : undefined);
    }
    signal.throwIfAborted();
    const network = this.network;
    return network(`${publicOrigin}${match.path}${url.search}`, {
      method,
      headers,
      cache: "no-store",
      credentials: extension.credentials,
      redirect: "error",
      referrerPolicy: "no-referrer",
      keepalive: init?.keepalive ?? original?.keepalive,
      body,
      signal,
    });
  };

  private unsupported(): Response {
    return Response.json({ detail: "Unsupported reader request" }, { status: 403 });
  }
}
