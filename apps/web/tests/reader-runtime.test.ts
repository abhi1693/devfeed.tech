// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

it("preserves the website's normal fetch and URL behavior by default", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json({ ok: true }));
  vi.stubGlobal("fetch", fetcher);
  const { readerLoginLink, readerRequest, readerLocation, readerPublicOrigin, readerWebsiteLink } =
    await import("@/lib/reader-runtime");
  const init = { credentials: "same-origin" as const };
  await readerRequest("/api/v1/feed", init);
  expect(fetcher).toHaveBeenCalledWith("/api/v1/feed", init);
  expect(readerLocation().href).toBe(window.location.href);
  expect(readerPublicOrigin()).toBe(window.location.origin);
  expect(readerWebsiteLink("/api/v1/user/auth/login")).toEqual({ href: "/api/v1/user/auth/login" });
  expect(readerLoginLink("/topics/typescript")).toEqual({
    href: "/login?return_to=%2Ftopics%2Ftypescript",
  });
});

it("uses the extension route for search and the website origin for sharing", async () => {
  const {
    configureReaderRuntime,
    readerRequest,
    readerLocation,
    readerLoginLink,
    readerPublicOrigin,
    readerWebsiteLink,
  } = await import("@/lib/reader-runtime");
  const request = vi.fn().mockResolvedValue(Response.json({ items: [] }));
  configureReaderRuntime({
    request,
    publicOrigin: "https://devfeed.tech",
    location: () => new URL("https://devfeed.tech/search?q=Rust&sort=newest"),
  });
  await readerRequest("/api/v1/feed");
  expect(request).toHaveBeenCalledWith("/api/v1/feed", undefined);
  expect(readerLocation().pathname).toBe("/search");
  expect(readerLocation().searchParams.get("sort")).toBe("newest");
  expect(new URL("/articles/rust", readerPublicOrigin()).href).toBe(
    "https://devfeed.tech/articles/rust",
  );
  expect(readerWebsiteLink("/api/v1/user/auth/login")).toEqual({
    href: "https://devfeed.tech/api/v1/user/auth/login",
    target: "_blank",
    rel: "noopener noreferrer",
  });
  expect(readerLoginLink("/topics/typescript", { register: true })).toEqual({
    href: "https://devfeed.tech/login?register=true&return_to=%2Fextension%2Flogin-complete",
    target: "_blank",
    rel: "noopener noreferrer",
  });
});
