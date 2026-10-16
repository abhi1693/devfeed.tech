import { expect, it } from "vitest";
import {
  documentApiUrl,
  documentReportFilename,
  documentResponseStatus,
  documentCacheStatus,
} from "./browser/document-latency-config.mjs";

it("keeps benchmark API requests on the configured origin while preserving filters", () => {
  const target = documentApiUrl("/v1/feed?cursor=next&q=c%2B%2B", "https://api.devfeed.test");
  expect(target.href).toBe("https://api.devfeed.test/v1/feed?cursor=next&q=c%2B%2B");
  expect(documentApiUrl("/v1/topics", "http://127.0.0.1:8000").href).toBe(
    "http://127.0.0.1:8000/v1/topics",
  );
});

it.each([
  "https://evil.test/v1/feed",
  "//evil.test/v1/feed",
  "/v1/../admin",
  "/v1/%2e%2e/admin",
  "/admin",
  undefined,
])("rejects a proxy target outside the public API namespace: %s", (path) => {
  expect(() => documentApiUrl(path, "https://api.devfeed.test")).toThrow();
});

it.each(["file:///etc/passwd", "https://user:password@api.devfeed.test"])(
  "rejects an invalid upstream configuration: %s",
  (origin) => {
    expect(() => documentApiUrl("/v1/feed", origin)).toThrow();
  },
);

it.each(["before", "after", "profile"])("uses a fixed report filename for %s", (phase) => {
  expect(documentReportFilename(phase)).toBe(`${phase}.json`);
});

it.each(["../outside", "../../before", "/tmp/report", "__proto__", ""])(
  "rejects arbitrary report paths: %s",
  (phase) => {
    expect(() => documentReportFilename(phase)).toThrow();
  },
);

it.each([100, 200, 599, "404"])("records a validated numeric HTTP status: %s", (value) => {
  expect(documentResponseStatus(value)).toBe(Number(value));
});

it.each([99, 600, 200.5, "<script>", Infinity, undefined])(
  "rejects invalid HTTP status metadata: %s",
  (value) => {
    expect(() => documentResponseStatus(value)).toThrow();
  },
);

it("records only known cache labels rather than arbitrary response header text", () => {
  for (const status of ["HIT", "MISS", "BYPASS", null])
    expect(documentCacheStatus(status)).toBe(status);
  expect(documentCacheStatus("unexpected remote text")).toBe("UNKNOWN");
});
