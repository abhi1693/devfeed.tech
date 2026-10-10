import { afterEach, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { proxy } from "@/proxy";

afterEach(() => vi.unstubAllEnvs());

it.each(["development", "production"])(
  "limits eval permission to development (%s)",
  async (mode) => {
    vi.stubEnv("NODE_ENV", mode);
    vi.stubEnv("DEVFEED_X_PIXEL_ENABLED", "true");
    vi.stubEnv("DEVFEED_USER_BASE_URL", "https://devfeed.tech");
    const response = await proxy(new NextRequest("https://devfeed.tech/latest"));
    const policy = response.headers.get("content-security-policy")!;
    expect(policy.includes("'unsafe-eval'")).toBe(mode === "development");
    expect(policy).not.toContain("ads-twitter.com");
    expect(policy).not.toContain("twitter.com");
  },
);

it("preserves attribution when Markdown negotiation returns a standard Response", async () => {
  vi.stubEnv("DEVFEED_X_PIXEL_ENABLED", "true");
  vi.stubEnv("DEVFEED_USER_BASE_URL", "https://devfeed.tech");
  const response = await proxy(
    new NextRequest("https://devfeed.tech/articles/demo.md?twclid=ad-click"),
  );
  expect(response.headers.get("set-cookie")).toContain("__Host-devfeed_user_x_click=ad-click");
  expect(response.headers.get("content-security-policy")).toContain("script-src");
});

it("captures the landing click before navigation with a host-only, private cookie", async () => {
  vi.stubEnv("DEVFEED_X_PIXEL_ENABLED", "true");
  vi.stubEnv("DEVFEED_USER_BASE_URL", "https://devfeed.tech");
  const response = await proxy(new NextRequest("https://devfeed.tech/login?twclid=ad-click-1"));
  const cookie = response.headers.get("set-cookie")!;
  expect(cookie).toContain("__Host-devfeed_user_x_click=ad-click-1");
  expect(cookie).toMatch(/HttpOnly/i);
  expect(cookie).toMatch(/Secure/i);
  expect(cookie).toMatch(/SameSite=lax/i);
  expect(cookie).not.toMatch(/Domain=/i);
  expect(response.headers.get("cache-control")).toBe("private, no-store");
});

it("replaces a previous click and uses a development cookie over HTTP", async () => {
  vi.stubEnv("DEVFEED_X_PIXEL_ENABLED", "true");
  vi.stubEnv("DEVFEED_USER_BASE_URL", "http://localhost:3000");
  const response = await proxy(
    new NextRequest("http://localhost:3000/login?twclid=new-click", {
      headers: { Cookie: "devfeed_user_x_click=old-click" },
    }),
  );
  expect(response.headers.get("set-cookie")).toContain("devfeed_user_x_click=new-click");
  expect(response.headers.get("set-cookie")).not.toMatch(/Secure|__Host-/i);
});

it.each(["false", "true"])(
  "ignores invalid clicks and avoids extending the same click's lifetime (%s)",
  async (flag) => {
    vi.stubEnv("DEVFEED_X_PIXEL_ENABLED", flag);
    vi.stubEnv("DEVFEED_USER_BASE_URL", "https://devfeed.tech");
    for (const query of [
      "",
      "?twclid=",
      "?twclid=bad%3Bvalue",
      "?twclid=bad%0A",
      "?twclid=" + "x".repeat(513),
    ]) {
      const response = await proxy(new NextRequest(`https://devfeed.tech/login${query}`));
      expect(response.headers.get("set-cookie")).toBeNull();
    }
    const response = await proxy(
      new NextRequest("https://devfeed.tech/login?twclid=same-click", {
        headers: { Cookie: "__Host-devfeed_user_x_click=same-click" },
      }),
    );
    expect(response.headers.get("set-cookie")).toBeNull();
    if (flag === "false") {
      expect(
        (await proxy(new NextRequest("https://devfeed.tech/login?twclid=valid-click"))).headers.get(
          "set-cookie",
        ),
      ).toBeNull();
    }
  },
);
