import { expect, it } from "vitest";
import {
  methodName,
  normalizeBody,
  normalizeMeta,
  normalizePayload,
  redactAuthText,
  routeName,
} from "@devfeed/telemetry/privacy";

const settings = {
  enabled: true,
  app: "web" as const,
  version: "1.2.3",
  environment: "production",
};

it.each([
  "/",
  "/login",
  "/register",
  "/search",
  "/articles",
  "/trending",
  "/read-later",
  "/latest",
  "/sources",
  "/sources/suggest",
  "/topics",
  "/legal",
  "/legal/privacy",
  "/legal/terms",
  "/settings/profile",
  "/settings/sources",
  "/settings/topics",
  "/settings/feed",
  "/settings/notifications",
  "/settings/appearance",
  "/news",
  "/tutorials",
  "/releases",
  "/comparisons",
  "/opinions",
  "/leaderboard",
  "/dev-card",
  "/mcp",
  "/mcp/authorize",
  "/extension/login-complete",
])("keeps the known page route %s bounded and removes queries", (route) => {
  expect(routeName(`https://devfeed.tech${route}?code=private`)).toBe(route);
  expect(routeName(`${route === "/" ? "" : route}/`)).toBe(route);
});

it.each([
  ["web", "/api/v1/feed", "/v1/feed"],
  ["web", "/api/v1/articles/private-article", "/v1/articles/:id"],
  ["web", "/api/v1/user/articles/private-id/bookmark", "/v1/user/articles/:id/bookmark"],
  ["admin", "/api/v1/admin/articles/private-id/review", "/v1/admin/articles/:id/review"],
  [
    "partner",
    "/api/v1/partner/accounts/private-account/products/private-product/ad-image",
    "/v1/partner/accounts/:id/products/:id/ad-image",
  ],
] as const)("labels the registered %s operation %s without identifiers", (app, path, label) => {
  expect(routeName(`${path}?code=private&account_id=private`, app)).toBe(label);
});

it.each(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])(
  "normalizes the %s method without accepting arbitrary labels",
  (method) => expect(methodName(method.toLowerCase())).toBe(method),
);

it("rejects unknown methods and routes and bounds assets and reader filters", () => {
  for (const value of [null, undefined, 42, {}, ""]) {
    expect(routeName(value)).toBe("unmatched");
    expect(methodName(value)).toBe("OTHER");
  }
  expect(methodName("CUSTOM-PRIVATE")).toBe("OTHER");
  expect(routeName("http://[")).toBe("unmatched");
  expect(routeName("/unknown/articles/private-id")).toBe("unmatched");
  expect(routeName("/articles/private-id/news")).toBe("unmatched");
  expect(routeName("/articles/private-id/news/extra")).toBe("unmatched");
  expect(routeName("/_next/static/chunks/private.js")).toBe("/_next/static/:asset");
  expect(routeName("/_next/image?url=private")).toBe("/_next/image");
  expect(routeName("/settings/custom")).toBe("unmatched");
});

it.each([
  "/content/sources/new",
  "/content/sources/import",
  "/taxonomy/relationships/discover",
  "/taxonomy/relationships/proposals",
])("preserves the registered admin operation %s", (path) => {
  expect(routeName(path, "admin")).toBe(path);
});

it.each(["edit", "delete", "review", "classify", "related", "history", "evidence"])(
  "bounds the admin %s action without losing its label",
  (action) => {
    expect(routeName(`/content/articles/private-id/${action}`, "admin")).toBe(
      `/content/articles/:id/${action}`,
    );
  },
);

it("keeps admin route boundaries strict", () => {
  expect(routeName("/content/sources/private-id/fetch", "admin")).toBe(
    "/content/sources/:id/fetch",
  );
  expect(routeName("/users/private-id/analysis", "admin")).toBe("/users/:id/analysis");
  expect(routeName("/jobs/analysis/topics/private-id/logs", "admin")).toBe(
    "/jobs/analysis/topics/:id/logs",
  );
  expect(routeName("/jobs/analysis", "admin")).toBe("/jobs/analysis");
  expect(routeName("/jobs/analysis/topics/private-id", "admin")).toBe("/jobs/analysis/topics/:id");
  expect(routeName("/taxonomy/topics/proposals/private-id", "admin")).toBe(
    "/taxonomy/topics/proposals/:id",
  );
  expect(routeName("/content/articles/private-id/unknown", "admin")).toBe("unmatched");
  expect(routeName("/content/articles/private-id/edit/extra", "admin")).toBe("unmatched");
  expect(routeName("/v1/private-resource/private-id")).toBe("unmatched");
});

it("scopes labels to their owning application", () => {
  expect(routeName("/settings?account_id=private", "web")).toBe("/settings/profile");
  expect(routeName("/settings/profile", "web")).toBe("/settings/profile");
  expect(routeName("/settings/private-account/audience", "partner")).toBe("/settings/:id/audience");
  expect(routeName("/settings/private-account/audience", "web")).toBe("unmatched");
  expect(routeName("/products/private-product/edit", "partner")).toBe("/products/:id/edit");
  expect(routeName("/products/private-product/edit", "admin")).toBe("unmatched");
  expect(
    routeName("/partnerships/accounts/private-account/assets/private-asset/edit", "admin"),
  ).toBe("/partnerships/accounts/:id/assets/:id/edit");
});

it("rejects spoofed or identifying Faro view names and preserves approved names", () => {
  const approved = normalizeMeta({ view: { id: "view-id", name: "/articles/:id" } }, settings);
  expect(approved.view).toEqual({ id: "view-id", name: "/articles/:id" });
  for (const name of [
    "/articles/private-slug",
    "/users/private-reader",
    "/settings?code=private",
    42,
  ])
    expect(normalizeMeta({ view: { name } }, settings).view?.name).toBe("unmatched");
  expect(normalizeMeta({ view: { name: "/partnerships" } }, settings).view?.name).toBe("unmatched");
});

it("accepts only approved semantic route attributes at the browser and receiver boundary", () => {
  const approved = {
    name: "route_change",
    timestamp: "2026-10-15T00:00:00Z",
    attributes: { route: "/users/:username" },
  };
  expect(normalizePayload("event", approved, settings)).toEqual(approved);
  const identifying = {
    ...approved,
    attributes: {
      route: "/users/private-reader",
      url: "/users/private-reader?q=private-query",
    },
  };
  expect(normalizePayload("event", identifying, settings)).toEqual({
    ...approved,
    attributes: { route: "unmatched" },
  });
  expect(normalizeBody({ events: [identifying] }, settings).events).toEqual([
    { ...approved, attributes: { route: "unmatched" } },
  ]);
  expect(
    normalizePayload("event", { ...approved, attributes: { route: "/partnerships" } }, settings),
  ).toMatchObject({ attributes: { route: "unmatched" } });
});

it.each([
  "access_token",
  "refresh-token",
  "id_token",
  "token",
  "client_secret",
  "code_verifier",
  "assertion",
  "client_assertion",
  "password",
  "passwd",
  "secret",
  "api_key",
  "authorization",
  "cookie",
  "set-cookie",
  "SAMLResponse",
  "oauth_token",
  "oauth_verifier",
  "device_code",
  "user_code",
])("redacts the %s credential in attributes, query strings and nested payloads", (field) => {
  expect(redactAuthText(`https://example.com/?${field}=private&topic=typescript`)).toBe(
    `https://example.com/?${field}=[REDACTED]&topic=typescript`,
  );
  expect(normalizePayload("event", { nested: [{ [field]: "private", keep: "public" }] })).toEqual({
    nested: [{ [field]: "[REDACTED]", keep: "public" }],
  });
});

it("sanitizes credential headers and URLs without destroying ordinary diagnostics", () => {
  expect(redactAuthText("Authorization: BEARER  abc12==")).toBe(
    "Authorization: BEARER  [REDACTED]",
  );
  expect(redactAuthText("Basic YWJjMTI=")).toBe("Basic [REDACTED]");
  expect(redactAuthText("https://user:password@example.com/path")).toBe(
    "https://[REDACTED]@example.com/path",
  );
  expect(redactAuthText("ordinary diagnostic message")).toBe("ordinary diagnostic message");
  expect(redactAuthText("code=private;state=private&q=public")).toBe(
    "code=[REDACTED];state=[REDACTED]&q=public",
  );
  for (const value of [null, 42, "private", { stringValue: "private" }]) {
    expect(normalizePayload("event", { key: "http.request.header.Authorization", value })).toEqual({
      key: "http.request.header.Authorization",
      value:
        value !== null && typeof value === "object" ? { stringValue: "[REDACTED]" } : "[REDACTED]",
    });
  }
});

it("bounds telemetry arrays and drops malformed entries while preserving valid diagnostics", () => {
  const entries = Array.from({ length: 101 }, (_, index) => ({ message: String(index) }));
  const result = normalizeBody(
    { logs: entries, events: [null, [], "bad", { name: "public" }] },
    settings,
  );
  expect(result.logs).toEqual(entries.slice(0, 100));
  expect(result.events).toEqual([{ name: "public" }]);
  expect(result.measurements).toEqual([]);
  expect(result.exceptions).toEqual([]);
  expect(result).not.toHaveProperty("traces");
  for (const value of [null, undefined, 42, "bad", []])
    expect(normalizePayload("event", value)).toBeNull();
  expect(normalizeBody([], settings)).toMatchObject({
    logs: [],
    events: [],
    meta: { app: { name: "devfeed-web" } },
  });
});

it.each(["exceptions", "measurements", "events", "logs"] as const)(
  "drops malformed %s while retaining redacted structured diagnostics",
  (collection) => {
    const diagnostic = { name: "public", message: "code=private&topic=typescript" };
    const result = normalizeBody(
      { [collection]: [null, [], "bad", 42, false, diagnostic] },
      settings,
    );
    expect(result[collection]).toEqual([
      { name: "public", message: "code=[REDACTED]&topic=typescript" },
    ]);
    expect(diagnostic.message).toBe("code=private&topic=typescript");
  },
);

it.each(["exception", "measurement", "log"])(
  "preserves ordinary route_change diagnostics of type %s",
  (type) => {
    const diagnostic = {
      name: "route_change",
      attributes: { route: "/articles/private-slug", component: "router" },
    };
    expect(normalizePayload(type, diagnostic, settings)).toEqual(diagnostic);
  },
);

it.each([undefined, null, "invalid", {}, 42])(
  "does not invent trace spans from malformed resourceSpans: %j",
  (resourceSpans) => {
    expect(normalizePayload("trace", { resourceSpans }, settings)).toEqual({
      resourceSpans: [],
    });
  },
);

it("uses trusted deployment labels for traces and preserves custom attributes", () => {
  const spoofed = ["service.name", "service.version", "deployment.environment.name"].map((key) => ({
    key,
    value: { stringValue: "spoofed" },
  }));
  const custom = { key: "custom", value: { stringValue: "public" } };
  const input = {
    resourceSpans: [{ resource: { attributes: [...spoofed, custom] }, scopeSpans: [] }],
  };
  expect(normalizePayload("trace", input, settings)).toEqual({
    resourceSpans: [
      {
        resource: {
          attributes: [
            custom,
            { key: "service.name", value: { stringValue: "devfeed-web-browser" } },
            { key: "service.version", value: { stringValue: "1.2.3" } },
            { key: "deployment.environment.name", value: { stringValue: "production" } },
          ],
        },
        scopeSpans: [],
      },
    ],
  });
  expect(input.resourceSpans[0].resource.attributes).toEqual([...spoofed, custom]);
  expect(normalizePayload("trace", input)).toEqual(input);
});

it("overwrites browser-supplied app labels and controls the sampling override", () => {
  const input = {
    app: { name: "spoofed", custom: "public" },
    session: { id: "session", attributes: { isSampled: true, custom: "public" } },
  };
  expect(normalizeMeta(input, settings)).toEqual({
    app: { name: "devfeed-web", version: "1.2.3", environment: "production", custom: "public" },
    session: { id: "session", attributes: { custom: "public" } },
  });
  expect(normalizeMeta(input, settings, true).session?.attributes).toEqual(
    input.session.attributes,
  );
  expect(normalizeMeta(null, settings)).not.toHaveProperty("session");
});
