// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { UserLogin } from "@/components/user-login";
import { pageDestination } from "@/lib/reader-destination";
import { decodeReaderLoginDestination } from "@devfeed/http/reader-destinations";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("announces navigation, prevents duplicate sign-ins, and resets when returning to the page", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json({ enabled: true, providers: ["google", "github"] })),
  );
  render(<UserLogin />);
  const google = await screen.findByRole("link", { name: "Google" });
  const github = screen.getByRole("link", { name: "GitHub" });
  const prevented: boolean[] = [];
  // Observe React's decision, then keep jsdom on this page.
  const observe = (event: Event) => {
    prevented.push(event.defaultPrevented);
    event.preventDefault();
  };
  document.addEventListener("click", observe);
  try {
    fireEvent.click(google);
    expect(screen.getByRole("status").textContent).toBe("Connecting to Google…");
    expect(google.getAttribute("aria-busy")).toBe("true");
    expect(github.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(github);
    expect(prevented).toEqual([false, true]);
    expect(screen.getByRole("status").textContent).toBe("Connecting to Google…");
    act(() => window.dispatchEvent(new Event("pageshow")));
    expect(google.hasAttribute("aria-busy")).toBe(false);
    expect(github.hasAttribute("aria-disabled")).toBe(false);
    fireEvent.click(github);
    expect(prevented).toEqual([false, true, false]);
    expect(screen.getByRole("status").textContent).toBe("Connecting to GitHub…");
  } finally {
    document.removeEventListener("click", observe);
  }
});

it("keeps modified provider clicks available without marking this tab as connecting", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json({ enabled: true, providers: ["google"] })),
  );
  render(<UserLogin />);
  const google = await screen.findByRole("link", { name: "Google" });
  const prevented: boolean[] = [];
  const observe = (event: Event) => {
    prevented.push(event.defaultPrevented);
    event.preventDefault();
  };
  document.addEventListener("click", observe);
  try {
    for (const modifier of [
      { ctrlKey: true },
      { metaKey: true },
      { shiftKey: true },
      { altKey: true },
      { button: 1 },
    ]) {
      fireEvent.click(google, modifier);
      expect(google.hasAttribute("aria-busy")).toBe(false);
      expect(screen.getByRole("status").textContent).toBe("");
    }
    expect(prevented).toEqual([false, false, false, false, false]);
  } finally {
    document.removeEventListener("click", observe);
  }
});

it("offers only configured identity providers and preserves the return path", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json({ enabled: true, providers: ["google", "github"] })),
  );
  render(<UserLogin returnTo="/topics/typescript" />);

  const google = await screen.findByRole("link", { name: "Google" });
  expect(google.getAttribute("href")).toBe(
    "/api/v1/user/auth/login?provider=google&return_to=%2Ftopics%2Ftypescript",
  );
  expect(screen.getByRole("link", { name: "GitHub" })).toBeTruthy();
  expect(screen.queryByRole("link", { name: "Create an account" })).toBeNull();
  expect(screen.getByRole("heading", { name: "Sign in to DevFeed" })).toBeTruthy();
});

it("keeps the existing hosted login as a fallback when direct providers are not configured", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json({ enabled: true, providers: [] })),
  );
  render(<UserLogin returnTo="/read-later" />);

  expect(
    (await screen.findByRole("link", { name: "Continue to sign in" })).getAttribute("href"),
  ).toBe("/api/v1/user/auth/login?return_to=%2Fread-later");
  expect(screen.getByRole("heading", { name: "Sign in to DevFeed" })).toBeTruthy();
});

it.each([
  "/latest?content_type=news&sort=most_liked",
  "/sources/company-engineering/tutorials?q=C%2B%2B+%26+SQL&sort=newest",
  "/search?section=articles&content_types=tutorial&date_from=2026-01-01",
  "/users/reader",
  "/mcp/authorize?request=" + "a".repeat(43),
])("offers provider links that preserve approved destination %s", async (returnTo) => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json({ enabled: true, providers: ["google"] })),
  );
  render(<UserLogin returnTo={returnTo} />);
  const link = await screen.findByRole("link", { name: "Google" });
  const url = new URL(link.getAttribute("href")!, "https://devfeed.tech");
  expect(url.searchParams.get("return_to")).toBe(returnTo);
  expect(decodeReaderLoginDestination(url.searchParams.get("return_to")!)).not.toBeNull();
});

it.each([
  "//elsewhere.test",
  "/my-feed",
  "/settings/unknown",
  "/search?q=one&q=two",
  "/topics/rust/unknown",
  "/latest?sort=latest",
  "/mcp/authorize?request=short",
])("uses reader home when an untrusted UI return destination is invalid: %s", async (returnTo) => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json({ enabled: true, providers: ["google"] })),
  );
  render(<UserLogin returnTo={returnTo} />);
  const link = await screen.findByRole("link", { name: "Google" });
  expect(
    new URL(link.getAttribute("href")!, "https://devfeed.tech").searchParams.get("return_to"),
  ).toBe("/");
});

it("accepts a typed navigation destination without reclassifying its URL in the component", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json({ enabled: true, providers: ["google"] })),
  );
  render(<UserLogin returnTo={pageDestination("/settings/notifications")} />);
  const link = await screen.findByRole("link", { name: "Google" });
  expect(
    new URL(link.getAttribute("href")!, "https://devfeed.tech").searchParams.get("return_to"),
  ).toBe("/settings/notifications");
});
