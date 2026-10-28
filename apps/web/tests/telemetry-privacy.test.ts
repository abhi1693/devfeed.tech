import { expect, it } from "vitest";
import { methodName, redactAuthText, routeName } from "@devfeed/telemetry/privacy";

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
])("redacts the %s credential in request URLs", (field) => {
  expect(redactAuthText(`https://example.com/?${field}=private&topic=typescript`)).toBe(
    `https://example.com/?${field}=[REDACTED]&topic=typescript`,
  );
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
});

it.each(["session_state", "RelayState"])("redacts %s callback state in request URLs", (field) => {
  expect(redactAuthText(`/callback?${field}=private&topic=public`)).toBe(
    `/callback?${field}=[REDACTED]&topic=public`,
  );
});

it("preserves UTF-8 and escapes punctuation while redacting an encoded callback", () => {
  const callback = "/callback?code=private&topic=日本語&label=don't";
  const expected = encodeURIComponent(
    callback.replace("code=private", "code=[REDACTED]"),
  ).replaceAll("'", "%27");
  expect(
    redactAuthText(`/login?return_to=${encodeURIComponent(callback).replaceAll("'", "%27")}`),
  ).toBe(`/login?return_to=${expected}`);
});

it("retains malformed encoded diagnostics without losing adjacent credentials", () => {
  expect(redactAuthText("/callback?topic=%FF&state=private")).toBe(
    "/callback?topic=%FF&state=[REDACTED]",
  );
});

it("bounds repeatedly encoded callback URLs", () => {
  let callback = "/callback?code=private";
  for (let depth = 0; depth < 7; depth++) callback = encodeURIComponent(callback);
  expect(redactAuthText(`/login?return_to=${callback}`)).not.toContain("private");
});
