/// <reference types="vite/client" />

import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import type { GatewayOperation } from "@devfeed/http/gateway";
import { partnerGatewayOperations } from "../src/lib/api/generated/partner-operations";

vi.mock("@devfeed/telemetry/propagation", () => ({ traceHeaders: () => ({}) }));

type Params = Record<string, string | string[]>;
type Handler = (
  request: Request,
  context: { params?: Promise<Params> },
) => Response | Promise<Response>;
type RouteModule = Record<string, unknown>;
const modules = import.meta.glob<RouteModule>("../src/app/api/**/route.ts", {
  eager: true,
});
const httpMethods = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"];
const origin = "https://partner.example";
const params = {
  account_id: "11111111-1111-4111-8111-111111111111",
  product_id: "22222222-2222-4222-8222-222222222222",
  provider: "catalog-connection",
};

function routeFile(operation: GatewayOperation) {
  const path = operation.path.replace(/\{([^}]+)\}/g, (_match, name: string) => {
    const parameter = operation.parameters.find((value) => value.name === name)!;
    return parameter.converter === "path" ? `[...${name}]` : `[${name}]`;
  });
  return `../src/app/api${path}/route.ts`;
}

const operations = Object.values(partnerGatewayOperations).map((operation) => ({
  operation,
  file: routeFile(operation),
}));

beforeEach(() => {
  vi.stubEnv("DEVFEED_PARTNER_API_URL", "http://partner-api:8004");
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", origin);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("generated concrete partner API routes", () => {
  it("has exactly one Next route module for every declared backend path and no catch-all proxy", () => {
    expect(Object.keys(modules).sort()).toEqual(
      [...new Set(operations.map(({ file }) => file))].sort(),
    );
  });

  it.each(operations)(
    "forwards $operation.operationId from its actual exported handler",
    async ({ operation, file }) => {
      const routeModule = modules[file];
      expect(routeModule).toBeDefined();
      expect(routeModule.dynamic).toBe("force-dynamic");
      expect(routeModule.runtime).toBe("nodejs");
      const handler = routeModule[operation.method] as Handler;
      expect(handler).toBeTypeOf("function");
      const boundParams = Object.fromEntries(
        operation.parameters.map(({ name }) => {
          expect(Object.hasOwn(params, name)).toBe(true);
          return [name, params[name as keyof typeof params]];
        }),
      );
      const fetcher = vi.fn().mockResolvedValue(Response.json({ ok: true }));
      vi.stubGlobal("fetch", fetcher);
      const response = await handler(
        new Request(`${origin}/api?offset=25`, {
          method: operation.method,
          headers: { origin, "x-csrf-token": "csrf", cookie: "partner_session=private" },
          ...(!["GET", "HEAD", "OPTIONS"].includes(operation.method) ? { body: "{}" } : {}),
        }),
        operation.parameters.length ? { params: Promise.resolve(boundParams) } : {},
      );
      expect(response.status).toBe(200);
      expect(fetcher).toHaveBeenCalledOnce();
      const expectedPath = operation.path.replace(/\{([^}]+)\}/g, (_match, name: string) =>
        encodeURIComponent(boundParams[name]),
      );
      expect(fetcher.mock.calls[0][0]).toBe(`http://partner-api:8004${expectedPath}?offset=25`);
      expect(fetcher.mock.calls[0][1].method).toBe(operation.method);
    },
  );

  it.each(Object.entries(modules))(
    "exposes only its declared methods and blocks implicit HEAD: %s",
    async (file, routeModule) => {
      const allowed = operations
        .filter((operation) => operation.file === file)
        .map(({ operation }) => operation.method);
      const exported = httpMethods.filter((method) => typeof routeModule[method] === "function");
      const expected = allowed.includes("GET") ? [...allowed, "HEAD"] : allowed;
      expect(exported.sort()).toEqual(expected.sort());
      expect(routeModule.OPTIONS).toBeUndefined();
      if (allowed.includes("GET")) {
        const fetcher = vi.fn();
        vi.stubGlobal("fetch", fetcher);
        const response = await (routeModule.HEAD as Handler)(
          new Request(`${origin}/api`, { method: "HEAD" }),
          { params: Promise.resolve({}) },
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

  it("rejects wrong methods even if a bound write handler is called directly", async () => {
    const file = routeFile(partnerGatewayOperations.partnerProductAdImageUpload);
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const response = await (modules[file].POST as Handler)(
      new Request(`${origin}/api`, { method: "GET" }),
      { params: Promise.resolve(params) },
    );
    expect(response.status).toBe(405);
    expect(response.headers.get("allow")).toBe("POST");
    expect(fetcher).not.toHaveBeenCalled();
  });
});
