// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { setBrowserTheme, useBrowserTheme } from "@devfeed/ui/browser-theme";
import { themeScript } from "@devfeed/ui/theme-script";
import { ThemeToggleButton } from "@devfeed/ui/theme-toggle";
import { PortalShell } from "../src/app/portal-shell";
import { partnerIdentity } from "./partner-fixtures";

const key = "devfeed:theme";
let media: EventTarget & { matches: boolean };

function ThemeControl() {
  return <ThemeToggleButton theme={useBrowserTheme()} onChange={setBrowserTheme} />;
}
function systemTheme(dark: boolean) {
  act(() => {
    media.matches = dark;
    media.dispatchEvent(new Event("change"));
  });
}
function externalTheme(theme: string | null) {
  act(() => {
    if (theme === null) localStorage.clear();
    else localStorage.setItem(key, theme);
    window.dispatchEvent(
      new StorageEvent("storage", { key: theme === null ? null : key, newValue: theme }),
    );
  });
}
const dark = () => document.documentElement.classList.contains("dark");

beforeEach(() => {
  localStorage.clear();
  document.documentElement.classList.remove("dark");
  media = Object.assign(new EventTarget(), { matches: true });
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => media),
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  document.documentElement.classList.remove("dark");
});

it("restores the saved theme in the actual partner header", () => {
  localStorage.setItem(key, "dark");
  render(
    <PortalShell identity={partnerIdentity()} onSignOut={vi.fn()}>
      <h1>Dashboard</h1>
    </PortalShell>,
  );
  expect(screen.getByRole("button", { name: "Theme: Dark. Switch to system theme" })).toBeTruthy();
  expect(dark()).toBe(true);
});

it("cycles all theme modes from the keyboard and persists each choice", async () => {
  const user = userEvent.setup();
  render(<ThemeControl />);
  await user.tab();
  expect(screen.getByRole("button").getAttribute("aria-label")).toBe(
    "Theme: System. Switch to light theme",
  );
  await user.keyboard("{Enter}");
  expect(dark()).toBe(false);
  expect(localStorage.getItem(key)).toBe("light");
  expect(screen.getByRole("button").getAttribute("title")).toBe(
    "Theme: Light. Switch to dark theme",
  );
  await user.keyboard(" ");
  expect(dark()).toBe(true);
  expect(localStorage.getItem(key)).toBe("dark");
  await user.keyboard("{Enter}");
  expect(localStorage.getItem(key)).toBe("system");
  expect(screen.getByRole("button").getAttribute("aria-label")).toBe(
    "Theme: System. Switch to light theme",
  );
});

it("follows system changes while respecting both explicit theme overrides", () => {
  render(<ThemeControl />);
  expect(dark()).toBe(true);
  systemTheme(false);
  expect(dark()).toBe(false);
  systemTheme(true);
  expect(dark()).toBe(true);
  act(() => setBrowserTheme("light"));
  systemTheme(false);
  systemTheme(true);
  expect(dark()).toBe(false);
  act(() => setBrowserTheme("dark"));
  systemTheme(false);
  expect(dark()).toBe(true);
});

it("restores an explicit choice after all controls unmount and mount again", () => {
  const first = render(<ThemeControl />);
  act(() => setBrowserTheme("light"));
  first.unmount();
  render(<ThemeControl />);
  expect(dark()).toBe(false);
  expect(screen.getByRole("button", { name: "Theme: Light. Switch to dark theme" })).toBeTruthy();
});

it("synchronizes another tab's changes and returns to system after storage is cleared", () => {
  render(<ThemeControl />);
  externalTheme("light");
  expect(dark()).toBe(false);
  expect(screen.getByRole("button", { name: "Theme: Light. Switch to dark theme" })).toBeTruthy();
  externalTheme("dark");
  expect(dark()).toBe(true);
  externalTheme(null);
  expect(screen.getByRole("button", { name: "Theme: System. Switch to light theme" })).toBeTruthy();
  systemTheme(false);
  expect(dark()).toBe(false);
});

it.each([null, "system", "invalid"])(
  "keeps prepaint and mounted system behavior consistent for stored value %s",
  (stored) => {
    if (stored !== null) localStorage.setItem(key, stored);
    new Function(themeScript)();
    expect(dark()).toBe(true);
    render(<ThemeControl />);
    expect(dark()).toBe(true);
    expect(
      screen.getByRole("button", { name: "Theme: System. Switch to light theme" }),
    ).toBeTruthy();
  },
);

it("keeps theme changes usable in memory when browser storage reads and writes are denied", async () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new DOMException("Storage is unavailable", "SecurityError");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new DOMException("Storage is unavailable", "SecurityError");
  });
  new Function(themeScript)();
  expect(dark()).toBe(true);
  render(<ThemeControl />);
  await userEvent.click(screen.getByRole("button"));
  expect(dark()).toBe(false);
  systemTheme(false);
  systemTheme(true);
  expect(dark()).toBe(false);
  await userEvent.click(screen.getByRole("button"));
  expect(dark()).toBe(true);
  expect(screen.getByRole("button", { name: "Theme: Dark. Switch to system theme" })).toBeTruthy();
});
