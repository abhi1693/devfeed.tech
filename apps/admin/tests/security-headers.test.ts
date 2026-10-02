import { expect, it } from "vitest";
import { NextRequest } from "next/server";
import { proxy } from "@/proxy";

it("uses a fresh script nonce and rejects metrics before rendering", () => {
  const first = proxy(new NextRequest("https://admin.devfeed.home/"));
  const second = proxy(new NextRequest("https://admin.devfeed.home/"));
  const csp = first.headers.get("content-security-policy") ?? "";

  expect(csp).toMatch(/script-src [^;]*'nonce-[A-Za-z0-9+/]{22}=='/);
  expect(csp).not.toMatch(/script-src [^;]*'unsafe-inline'/);
  expect(csp).toContain("default-src 'self'");
  expect(csp).toContain("form-action 'self'");
  expect(csp).toContain("frame-ancestors 'none'");
  expect(second.headers.get("content-security-policy")).not.toBe(csp);
  expect(first.headers.get("x-middleware-request-content-security-policy")).toBe(csp);
  expect(proxy(new NextRequest("https://admin.devfeed.home/metrics")).status).toBe(404);
});
