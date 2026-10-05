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
  "/my-feed",
  "/latest",
  "/sources",
  "/sources/suggest",
  "/topics",
  "/legal",
  "/legal/privacy",
  "/legal/terms",
  "/settings",
  "/settings/profile",
  "/settings/sources",
  "/settings/topics",
  "/settings/feed",
  "/settings/notifications",
  "/settings/appearance",
  "/queues",
  "/workers",
  "/start",
  "/news",
  "/tutorials",
  "/research",
  "/videos",
  "/podcasts",
  "/content",
  "/taxonomy",
  "/jobs",
  "/jobs/enrichment",
  "/users",
])("keeps the known page route %s bounded and removes queries", (route) => {
  expect(routeName(`https://devfeed.tech${route}?code=private`)).toBe(route);
  expect(routeName(`${route === "/" ? "" : route}/`)).toBe(route);
});

it.each([
  "feed",
  "search",
  "articles",
  "sources",
  "topics",
  "tags",
  "workers",
  "jobs",
  "queues",
  "overview",
  "settings",
  "auth",
  "users",
  "notifications",
  "relationships",
  "topic-discovery",
  "research",
  "categories",
  "bookmarks",
  "preferences",
  "subscriptions",
  "account",
  "profile",
])("bounds public, user and admin API paths for %s", (resource) => {
  expect(routeName(`/v1/${resource}`)).toBe(`/v1/${resource}`);
  expect(routeName(`/api/v1/user/${resource}/private-id`)).toBe(`/v1/user/${resource}/:path`);
  expect(routeName(`/v1/admin/${resource}/private-id/action`)).toBe(`/v1/admin/${resource}/:path`);
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
  expect(routeName("/articles/private-id/news")).toBe("/articles/:id/:type");
  expect(routeName("/articles/private-id/news/extra")).toBe("unmatched");
  expect(routeName("/_next/static/chunks/private.js")).toBe("/_next/static/:asset");
  expect(routeName("/_next/image?url=private")).toBe("/_next/image");
  expect(routeName("/settings/custom")).toBe("/settings/:section");
});

it.each(["new", "import", "discover", "proposals"])(
  "preserves the admin %s operation",
  (operation) => {
    expect(routeName(`/content/sources/${operation}`)).toBe(`/content/sources/${operation}`);
  },
);

it.each([
  "edit",
  "delete",
  "enrich",
  "review",
  "classify",
  "fetch",
  "analysis",
  "related",
  "history",
  "evidence",
  "logs",
  "relevance",
  "topics",
  "sources",
  "likes",
  "interests",
  "recommendations",
])("bounds the admin %s action without losing its label", (action) => {
  expect(routeName(`/content/articles/private-id/${action}`)).toBe(
    `/content/articles/:id/${action}`,
  );
});

it("keeps admin route boundaries strict", () => {
  expect(routeName("/jobs/analysis")).toBe("/jobs/analysis");
  expect(routeName("/jobs/analysis/topics/private-id")).toBe("/jobs/analysis/topics/:id");
  expect(routeName("/taxonomy/topics/proposals/private-id")).toBe("/taxonomy/topics/proposals/:id");
  expect(routeName("/content/articles/private-id/unknown")).toBe("unmatched");
  expect(routeName("/content/articles/private-id/edit/extra")).toBe("unmatched");
  expect(routeName("/v1/private-resource/private-id")).toBe("unmatched");
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
