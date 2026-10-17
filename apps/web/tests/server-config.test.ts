import { afterEach, expect, it, vi } from "vitest";
import {
  extensionOriginAllowed,
  publicApiOrigin,
  publicSiteOrigin,
  userApiOrigin,
  userWebOrigin,
  userRequestOriginAllowed,
} from "@/lib/server/config";

afterEach(() => vi.unstubAllEnvs());

it.each([
  "",
  "ftp://example.com",
  "https://user@example.com",
  "https://:password@example.com",
  "https://example.com/path",
  "https://example.com?code=secret",
  "https://example.com#token",
])("rejects non-origin authentication configuration: %s", (origin) => {
  vi.stubEnv("DEVFEED_USER_API_URL", origin);
  vi.stubEnv("DEVFEED_USER_BASE_URL", origin);
  expect(userApiOrigin).toThrow(/DEVFEED_USER_API_URL/);
  expect(userWebOrigin).toThrow(/DEVFEED_USER_BASE_URL/);
});

it("normalizes user origins and uses public defaults only when configuration is absent", () => {
  vi.stubEnv("DEVFEED_USER_API_URL", "http://USER-API:8002/");
  vi.stubEnv("DEVFEED_USER_BASE_URL", "https://EXAMPLE.com:443/");
  expect(userApiOrigin()).toBe("http://user-api:8002");
  expect(userWebOrigin()).toBe("https://example.com");
  expect(publicSiteOrigin()).toBe("https://example.com");
  vi.stubEnv("DEVFEED_USER_BASE_URL", "");
  vi.stubEnv("DEVFEED_PUBLIC_API_URL", undefined);
  expect(publicSiteOrigin()).toBe("https://devfeed.tech");
  expect(publicApiOrigin()).toBe("http://127.0.0.1:8000");
  vi.stubEnv("DEVFEED_PUBLIC_API_URL", "https://api.example.com");
  expect(publicApiOrigin()).toBe("https://api.example.com");
});

it.each(
  [
    null,
    {},
    [123],
    [["a".repeat(32)]],
    ["a".repeat(31)],
    ["a".repeat(33)],
    ["q".repeat(32)],
    ["A".repeat(32)],
  ].map((ids) => ({ ids })),
)("rejects malformed extension allowlists: $ids", ({ ids }) => {
  vi.stubEnv("DEVFEED_USER_EXTENSION_IDS", JSON.stringify(ids));
  expect(() => extensionOriginAllowed(`chrome-extension://${"a".repeat(32)}`)).toThrow(
    "DEVFEED_USER_EXTENSION_IDS must contain exact Chrome extension IDs",
  );
});

it("requires an exact extension origin and defaults to no trusted extensions", () => {
  vi.stubEnv("DEVFEED_USER_EXTENSION_IDS", undefined);
  expect(extensionOriginAllowed(null)).toBe(false);
  vi.stubEnv("DEVFEED_USER_EXTENSION_IDS", JSON.stringify(["p".repeat(32)]));
  expect(extensionOriginAllowed(`chrome-extension://${"p".repeat(32)}`)).toBe(true);
  expect(extensionOriginAllowed(`chrome-extension://${"p".repeat(32)}/`)).toBe(false);
  expect(extensionOriginAllowed(`https://${"p".repeat(32)}`)).toBe(false);
});

it("trusts only explicitly configured additional reader origins", () => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", "http://localhost:3000");
  vi.stubEnv("DEVFEED_USER_EXTENSION_IDS", "[]");
  vi.stubEnv("DEVFEED_USER_ALLOWED_ORIGINS", "[]");
  expect(userRequestOriginAllowed("http://192.0.2.10:3000")).toBe(false);
  vi.stubEnv(
    "DEVFEED_USER_ALLOWED_ORIGINS",
    '["http://192.0.2.10:3000", "https://EXAMPLE.COM:443/"]',
  );
  expect(userRequestOriginAllowed("http://192.0.2.10:3000")).toBe(true);
  expect(userRequestOriginAllowed("https://example.com")).toBe(true);
  expect(userRequestOriginAllowed("http://192.0.2.11:3000")).toBe(false);
  expect(userRequestOriginAllowed(null)).toBe(false);
  expect(userWebOrigin()).toBe("http://localhost:3000");
});
it.each([null, {}, "http://example.com", 42])(
  "rejects additional reader origins that are not a list: %j",
  (values) => {
    vi.stubEnv("DEVFEED_USER_BASE_URL", "http://localhost:3000");
    vi.stubEnv("DEVFEED_USER_EXTENSION_IDS", "[]");
    vi.stubEnv("DEVFEED_USER_ALLOWED_ORIGINS", JSON.stringify(values));
    expect(() => userRequestOriginAllowed("http://localhost:3000")).toThrow(
      "DEVFEED_USER_ALLOWED_ORIGINS must be an origin list",
    );
  },
);

it.each(
  [
    null,
    42,
    ["https://example.com"],
    "*",
    "null",
    "ftp://example.com",
    "otherhttps://example.com",
    "https://user@example.com",
    "https://@example.com",
    "https://*.example.com",
    "https://example.com/path",
    "https://example.com/.",
    "https://example.com?",
    "https://example.com#",
    "https://example.com:",
    " https://example.com",
    "https://example.com\\path",
    "https://example.com\u0000",
    "https://example.com\u007f",
  ].map((value) => ({ value })),
)("rejects invalid additional reader origins: $value", ({ value }) => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", "http://localhost:3000");
  vi.stubEnv("DEVFEED_USER_EXTENSION_IDS", "[]");
  vi.stubEnv("DEVFEED_USER_ALLOWED_ORIGINS", JSON.stringify([value]));
  expect(() => userRequestOriginAllowed("http://localhost:3000")).toThrow(
    "DEVFEED_USER_ALLOWED_ORIGINS must contain exact HTTP(S) origins",
  );
});

it("rejects additional reader origins with invalid URL ports", () => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", "http://localhost:3000");
  vi.stubEnv("DEVFEED_USER_EXTENSION_IDS", "[]");
  vi.stubEnv("DEVFEED_USER_ALLOWED_ORIGINS", '["https://example.com:99999"]');
  expect(() => userRequestOriginAllowed("http://localhost:3000")).toThrow("Invalid URL");
});
