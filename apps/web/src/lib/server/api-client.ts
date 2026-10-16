import "server-only";
import { traceHeaders } from "@devfeed/telemetry/propagation";
import { publicReadCacheControl } from "../public-read-cache";
import { publicApiOrigin, userApiOrigin, xPixelEnabled } from "./config";
import { ReaderCookies } from "./reader-cookies";

export class UserApiError extends Error {
  constructor(public status: number) {
    super("The requested service is unavailable");
  }
}

type ReadOptions = { signal?: AbortSignal };
type TransportPolicy = {
  origin: () => string;
  cacheControl: string;
  deadlineMs: number;
};

/** Keep credentials and cache policy attached to the client, independent of endpoint names. */
class JsonApiClient {
  constructor(private readonly policy: TransportPolicy) {}

  async read<T>(path: string, options: ReadOptions, cookie = ""): Promise<T> {
    let response: Response;
    try {
      const origin = new URL(this.policy.origin());
      const url = new URL(path, origin);
      if (
        !["http:", "https:"].includes(origin.protocol) ||
        origin.username ||
        origin.password ||
        origin.pathname !== "/" ||
        origin.search ||
        origin.hash ||
        !path.startsWith("/") ||
        path.startsWith("//") ||
        path.includes("\\") ||
        url.origin !== origin.origin ||
        url.hash ||
        url.username ||
        url.password
      )
        throw new Error("API reads require a path on the configured origin");
      const deadline = AbortSignal.timeout(this.policy.deadlineMs);
      response = await fetch(url, {
        cache: "no-store",
        signal: options.signal ? AbortSignal.any([options.signal, deadline]) : deadline,
        headers: {
          Accept: "application/json",
          "Cache-Control": this.policy.cacheControl,
          ...(cookie ? { Cookie: cookie } : {}),
          ...traceHeaders(),
        },
        redirect: "error",
      });
    } catch {
      throw new UserApiError(503);
    }
    if (!response.ok) throw new UserApiError(response.status);
    try {
      return (await response.json()) as T;
    } catch {
      throw new UserApiError(502);
    }
  }
}

type PublicApiClientOptions = {
  deadlineMs?: number;
  upstreamCache?: "public" | "fresh";
};

/** Public reads never accept browser cookies or authorization headers. */
export class PublicApiClient {
  private readonly client: JsonApiClient;

  constructor({ deadlineMs = 8000, upstreamCache = "public" }: PublicApiClientOptions = {}) {
    this.client = new JsonApiClient({
      origin: publicApiOrigin,
      cacheControl: upstreamCache === "fresh" ? "no-store" : publicReadCacheControl,
      deadlineMs,
    });
  }

  read<T>(path: string, options: ReadOptions = {}): Promise<T> {
    return this.client.read<T>(path, options);
  }
}

/** User-service reads remain private even when the reader is anonymous. */
export class PrivateApiClient {
  private readonly client = new JsonApiClient({
    origin: userApiOrigin,
    cacheControl: "no-store",
    deadlineMs: 8000,
  });
  private readonly cookies = new ReaderCookies(xPixelEnabled);

  read<T>(path: string, options: ReadOptions & { cookie?: string } = {}): Promise<T> {
    return this.client.read<T>(path, options, this.cookies.filter(options.cookie ?? "", "reader"));
  }
}

export const publicApi = new PublicApiClient();
export const privateApi = new PrivateApiClient();
