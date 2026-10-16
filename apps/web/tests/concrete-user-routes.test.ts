/// <reference types="vite/client" />

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { GatewayOperation } from "@devfeed/http/gateway";
import { userGatewayOperations } from "@/lib/api/generated/user-operations";

vi.mock("@devfeed/telemetry/propagation", () => ({ traceHeaders: () => ({}) }));

type Params = Record<string, string | string[]>;
type Handler = (
  request: Request,
  context: { params?: Promise<Params> },
) => Response | Promise<Response>;
type RouteModule = Record<string, unknown>;
const modules = import.meta.glob<RouteModule>("../src/app/api/v1/user/**/route.ts", {
  eager: true,
});
const allApiRoutes = import.meta.glob("../src/app/api/**/route.ts");
const methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"];
const origin = "https://user.example";
const readerCookies =
  "devfeed_user_session=session; __Host-devfeed_user_state=state; devfeed_user_visitor=visitor";
const parameters = {
  article_id: "11111111-1111-4111-8111-111111111111",
  topic_id: "22222222-2222-4222-8222-222222222222",
  source_id: "33333333-3333-4333-8333-333333333333",
  grant_id: "44444444-4444-4444-8444-444444444444",
  request_id: "disposable-request-id",
  username: "reader",
  notification_id: `notif_${"a".repeat(26)}`,
  broadcast_id: `bcast_${"b".repeat(26)}`,
} satisfies Params;

function routeFile(operation: GatewayOperation) {
  const path = operation.path.replace(/\{([^}]+)\}/g, (_match, name: string) => {
    const parameter = operation.parameters.find((value) => value.name === name)!;
    return parameter.converter === "path" ? `[...${name}]` : `[${name}]`;
  });
  return `../src/app/api${path}/route.ts`;
}

const operations = Object.values(userGatewayOperations).map((operation) => ({
  operation,
  file: routeFile(operation),
}));

beforeEach(() => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", origin);
  vi.stubEnv("DEVFEED_USER_API_URL", "http://user-api:8002");
  vi.stubEnv("DEVFEED_USER_EXTENSION_IDS", "[]");
  vi.stubEnv("DEVFEED_X_PIXEL_ENABLED", "true");
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("generated concrete reader operations", () => {
  it("mounts every exported user path once without retaining a general API catch-all", () => {
    expect(Object.keys(modules).sort()).toEqual(
      [...new Set(operations.map(({ file }) => file))].sort(),
    );
    expect(Object.hasOwn(allApiRoutes, "../src/app/api/[...path]/route.ts")).toBe(false);
    expect(
      Object.hasOwn(
        allApiRoutes,
        "../src/app/api/v1/user/notifications/chimely/v1/inbox/[...path]/route.ts",
      ),
    ).toBe(false);
    expect(
      operations.filter(({ operation }) => operation.operationId.startsWith("userInbox")),
    ).toHaveLength(17);
    expect(
      operations
        .filter(({ operation }) => operation.policy.cookiePolicy === "reader-attribution")
        .map(({ operation }) => operation.operationId),
    ).toEqual(["user_auth_login"]);
  });

  it.each(operations)(
    "wires $operation.operationId to its concrete upstream and cookie policy",
    async ({ operation, file }) => {
      const routeModule = modules[file];
      expect(routeModule).toBeDefined();
      expect(routeModule.dynamic).toBe("force-dynamic");
      expect(routeModule.runtime).toBe("nodejs");
      const handler = routeModule[operation.method] as Handler;
      expect(handler).toBeTypeOf("function");
      const boundParams = Object.fromEntries(
        operation.parameters.map(({ name }) => {
          expect(Object.hasOwn(parameters, name)).toBe(true);
          return [name, parameters[name as keyof typeof parameters]];
        }),
      );
      const fetcher = vi.fn().mockResolvedValue(Response.json({ items: [] }));
      vi.stubGlobal("fetch", fetcher);
      const response = await handler(
        new Request(`${origin}/api?offset=25`, {
          method: operation.method,
          headers: {
            Origin: origin,
            "X-CSRF-Token": "csrf",
            Cookie: `${readerCookies}; _twclid=click; devfeed_admin_session=private`,
          },
          ...(!["GET", "HEAD", "OPTIONS"].includes(operation.method)
            ? { body: '{"surface":"feed"}' }
            : {}),
        }),
        operation.parameters.length ? { params: Promise.resolve(boundParams) } : {},
      );
      expect(response.status).toBe(200);
      expect(fetcher).toHaveBeenCalledOnce();
      const expectedPath = operation.path.replace(/\{([^}]+)\}/g, (_match, name: string) => {
        const value = boundParams[name];
        return Array.isArray(value)
          ? value.map(encodeURIComponent).join("/")
          : encodeURIComponent(value);
      });
      const [url, init] = fetcher.mock.calls[0];
      expect(url).toBe(`http://user-api:8002${expectedPath}?offset=25`);
      expect(init.method).toBe(operation.method);
      expect(init.headers.get("cookie")).toBe(
        operation.policy.cookiePolicy === "reader-attribution"
          ? `${readerCookies}; _twclid=click`
          : readerCookies,
      );
    },
  );

  it.each(Object.entries(modules))(
    "exports the exact backend methods, blocks implicit HEAD and leaves OPTIONS native: %s",
    async (file, routeModule) => {
      const allowed: string[] = operations
        .filter((value) => value.file === file)
        .map(({ operation }) => operation.method);
      const exported = methods.filter((method) => typeof routeModule[method] === "function");
      const expected =
        allowed.includes("GET") && !allowed.includes("HEAD") ? [...allowed, "HEAD"] : allowed;
      expect(exported.sort()).toEqual(expected.sort());
      expect(routeModule.OPTIONS).toBeUndefined();
      if (allowed.includes("GET") && !allowed.includes("HEAD")) {
        const fetcher = vi.fn();
        vi.stubGlobal("fetch", fetcher);
        const response = await (routeModule.HEAD as Handler)(
          new Request(`${origin}/api`, { method: "HEAD" }),
          {},
        );
        expect(response.status).toBe(405);
        expect(
          new Set(
            response.headers
              .get("allow")!
              .split(",")
              .map((value) => value.trim()),
          ),
        ).toEqual(new Set(allowed));
        expect(fetcher).not.toHaveBeenCalled();
      }
    },
  );
});
