// @vitest-environment jsdom
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ErrorPage from "../src/app/error";

const originalUrl = window.location.href;

beforeEach(() => {
  localStorage.clear();
  document.documentElement.classList.remove("dark");
});

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", originalUrl);
  localStorage.clear();
  document.documentElement.classList.remove("dark");
  vi.unstubAllGlobals();
});

describe("partner portal error recovery", () => {
  it("provides a standalone branded recovery screen without a session or router provider", () => {
    render(<ErrorPage />);

    const title = "Could not load your partner portal";
    const main = screen.getByRole("main", { name: title });
    const panel = within(main).getByRole("region", { name: title });
    expect(within(panel).getByRole("heading", { level: 1, name: title })).toBeTruthy();
    expect(
      within(main).getByRole("link", { name: "DevFeed Partners home" }).getAttribute("href"),
    ).toBe("/");
    expect(within(panel).getByText(/return to this page with your current filters/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /retry|try again/i })).toBeNull();
  });

  it.each([
    "/products?account_id=account-123&q=developer+tools&offset=20",
    "/connections/connection-123/edit?account_id=account-456",
    "/performance?period=7&ad_format=engagement",
  ])("retries %s with a native fresh-document link preserving the path and query", (url) => {
    window.history.replaceState(null, "", url);
    render(<ErrorPage />);

    const retry = screen.getByText("Try again", { selector: "a" }) as HTMLAnchorElement;
    expect(retry.getAttribute("href")).toBe("");
    expect(retry.href).toBe(window.location.href);
    expect(retry.getAttribute("data-slot")).toBe("button");
    expect(retry.getAttribute("target")).toBeNull();
    expect(retry.getAttribute("download")).toBeNull();
  });

  it.each([
    { stored: "dark", systemDark: false, dark: true },
    { stored: "light", systemDark: true, dark: false },
    { stored: "system", systemDark: true, dark: true },
    { stored: null, systemDark: true, dark: true },
  ])(
    "restores and maintains the $stored theme when the error document has not applied it",
    ({ stored, systemDark, dark }) => {
      if (stored !== null) localStorage.setItem("devfeed:theme", stored);
      const media = Object.assign(new EventTarget(), { matches: systemDark });
      vi.stubGlobal(
        "matchMedia",
        vi.fn(() => media),
      );
      document.documentElement.classList.toggle("dark", !dark);

      render(<ErrorPage />);

      expect(document.documentElement.classList.contains("dark")).toBe(dark);
      act(() => {
        media.matches = !systemDark;
        media.dispatchEvent(new Event("change"));
      });
      expect(document.documentElement.classList.contains("dark")).toBe(
        stored === "dark" || (stored !== "light" && !systemDark),
      );
    },
  );
});
