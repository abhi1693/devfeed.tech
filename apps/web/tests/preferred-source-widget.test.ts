// @vitest-environment jsdom
import { runInNewContext } from "node:vm";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { GET } from "@/app/preferred-source/widget/route";
import { preferredSourceWidgetPolicy } from "@/lib/server/preferred-source-widget";
import { proxy } from "@/proxy";

const origin = "https://devfeed.tech";
const publisher = "https://news.google.com/swg/js/v1/publisher.js";
const icon = "https://www.gstatic.com/images/branding/productlogos/googleg_gradient/v1/24px.svg";
const request = (query = "", headers?: HeadersInit) =>
  new Request(`${origin}/preferred-source/widget${query}`, { headers });
const directives = (policy: string) =>
  Object.fromEntries(
    policy.split(";").map((part) => {
      const [name, ...sources] = part.trim().split(/\s+/);
      return [name, sources];
    }),
  );

beforeEach(() => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", origin);
  vi.stubEnv("DEVFEED_USER_ALLOWED_ORIGINS", "[]");
  vi.stubEnv("NODE_ENV", "production");
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

it("returns private non-indexable HTML without making server-side Google requests", async () => {
  const network = vi.fn(() => {
    throw new Error("Widget rendering must not contact Google");
  });
  vi.stubGlobal("fetch", network);
  const response = GET(request());
  expect(response.status).toBe(200);
  expect(response.headers.get("content-type")).toBe("text/html; charset=utf-8");
  expect(response.headers.get("cache-control")).toBe("private, no-store");
  expect(response.headers.get("x-robots-tag")).toBe("noindex, nofollow");
  expect(response.headers.get("x-content-type-options")).toBe("nosniff");
  expect(response.headers.get("referrer-policy")).toBe("strict-origin-when-cross-origin");
  expect(await response.text()).toContain("google-add-preferred-source-btn");
  expect(network).not.toHaveBeenCalled();
});

it.each([
  ["", "light"],
  ["?theme=light", "light"],
  ["?theme=dark", "dark"],
  ["?theme=DARK", "light"],
  ["?theme=other", "light"],
  ["?theme=%22%3E%3Cscript%20src%3Dhttps%3A%2F%2Fevil.example%3E", "light"],
])("allows only the supported theme for %s", async (query, expected) => {
  const html = await GET(request(query)).text();
  const dom = new DOMParser().parseFromString(html, "text/html");
  try {
    expect(dom.querySelector("[google-add-preferred-source-btn]")?.getAttribute("data-theme")).toBe(
      expected,
    );
    expect(html).not.toContain("evil.example");
    expect(dom.querySelectorAll("script")).toHaveLength(1);
  } finally {
    dom.replaceChildren();
  }
});

it("keeps a narrow widget-only resource policy without general Google origins", () => {
  const policy = directives(preferredSourceWidgetPolicy("test-nonce"));
  expect(policy["default-src"]).toEqual(["'none'"]);
  expect(policy["script-src"]).toEqual(["'nonce-test-nonce'", publisher]);
  expect(policy["script-src-attr"]).toEqual(["'none'"]);
  expect(policy["style-src"]).toEqual(["'unsafe-inline'"]);
  expect(policy["img-src"]).toEqual([icon]);
  expect(policy["frame-src"]).toEqual([
    "https://news.google.com/swg/ui/v1/serviceiframe",
    "https://news.google.com/swg/ui/v1/toastiframe",
  ]);
  expect(policy["connect-src"]).toEqual(["'none'"]);
  expect(policy["object-src"]).toEqual(["'none'"]);
  expect(policy["base-uri"]).toEqual(["'none'"]);
  expect(policy["form-action"]).toEqual(["'none'"]);
  expect(policy["frame-ancestors"]).toEqual(["'self'"]);
});

it("uses the same fresh nonce in the policy, inline bootstrap and dynamically loaded SDK", async () => {
  const first = GET(request());
  const second = GET(request());
  const firstPolicy = directives(first.headers.get("content-security-policy")!);
  const nonce = firstPolicy["script-src"][0].match(/^'nonce-([^']+)'$/)?.[1];
  expect(nonce).toMatch(/^[A-Za-z0-9+/]{22}==$/);
  expect(second.headers.get("content-security-policy")).not.toBe(
    first.headers.get("content-security-policy"),
  );
  const dom = new DOMParser().parseFromString(await first.text(), "text/html");
  try {
    const document = dom;
    const bootstrap = document.querySelector("script")!;
    expect(bootstrap.nonce).toBe(nonce);
    Object.defineProperty(document, "currentScript", { value: bootstrap });
    // Execute the delivered bootstrap against an inert document. Its SDK script
    // is inspected below, without loading or executing any remote resource.
    runInNewContext(bootstrap.textContent!, {
      document,
      Element,
      MutationObserver,
      ResizeObserver,
      window,
      parent: window,
      location,
    });
    const external = document.querySelector<HTMLScriptElement>("script[src]")!;
    expect(external.src).toBe(publisher);
    expect(external.nonce).toBe(nonce);
    expect(document.querySelectorAll("script[src]")).toHaveLength(1);
  } finally {
    dom.replaceChildren();
  }
});

it("shares the proxy-generated widget nonce instead of trusting a caller's policy", async () => {
  const forwarded = await proxy(
    new NextRequest(`${origin}/preferred-source/widget?theme=dark`, {
      headers: {
        "content-security-policy": "script-src 'nonce-caller-controlled' https://evil.example",
      },
    }),
  );
  const policy = forwarded.headers.get("content-security-policy")!;
  expect(policy).not.toContain("caller-controlled");
  expect(policy).not.toContain("evil.example");
  expect(forwarded.headers.get("x-middleware-request-content-security-policy")).toBe(policy);
  const response = GET(request("?theme=dark", { "content-security-policy": policy }));
  expect(response.headers.get("content-security-policy")).toBe(policy);
  const dom = new DOMParser().parseFromString(await response.text(), "text/html");
  try {
    const nonce = dom.querySelector("script")!.nonce;
    expect(directives(policy)["script-src"]).toContain(`'nonce-${nonce}'`);
  } finally {
    dom.replaceChildren();
  }
});

it("does not reflect a malformed incoming nonce or unrelated URL parameters", async () => {
  const response = GET(
    request("?theme=dark&source=https%3A%2F%2Fevil.example", {
      "content-security-policy": `script-src 'nonce-\"><img src=evil.example>'`,
      cookie: "private-session-cookie",
    }),
  );
  const policy = response.headers.get("content-security-policy")!;
  const html = await response.text();
  expect(policy).not.toContain("evil.example");
  expect(html).not.toMatch(/evil\.example|private-session-cookie/);
  expect(directives(policy)["script-src"][0]).toMatch(/^'nonce-[A-Za-z0-9+/]{22}=='$/);
});

it("keeps Google scripts and inline style blocks out of the parent reader policy", async () => {
  const parent = await proxy(new NextRequest(`${origin}/latest`));
  const policy = directives(parent.headers.get("content-security-policy")!);
  expect(policy["script-src-elem"].some((source) => source.startsWith("'nonce-"))).toBe(true);
  expect(policy["script-src-elem"]).not.toContain("'unsafe-inline'");
  expect(policy["style-src-elem"]).not.toContain("'unsafe-inline'");
  expect(policy["script-src-elem"].join(" ")).not.toContain("news.google.com");
  expect(policy["frame-src"]).toEqual([`${origin}/preferred-source/widget`]);
  expect(policy["connect-src"].join(" ")).not.toContain("news.google.com");
  expect(policy["script-src-attr"]).toEqual(["'none'"]);
  expect(policy["object-src"]).toEqual(["'none'"]);
});
