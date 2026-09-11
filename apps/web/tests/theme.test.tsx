// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { ThemeToggle } from "@/components/theme-toggle";
import { themeScript } from "@/lib/theme";

let system: EventTarget & { matches: boolean };
beforeEach(() => {
  localStorage.clear();
  document.documentElement.classList.remove("dark");
  system = Object.assign(new EventTarget(), { matches: true });
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => system),
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("applies the saved theme before hydration, falling back to the system for invalid values", () => {
  localStorage.setItem("devfeed:theme", "light");
  new Function(themeScript)();
  expect(document.documentElement.classList.contains("dark")).toBe(false);
  localStorage.setItem("devfeed:theme", "invalid");
  new Function(themeScript)();
  expect(document.documentElement.classList.contains("dark")).toBe(true);
});

it("follows system changes until the user chooses and remembers that choice", () => {
  const view = render(<ThemeToggle />);
  expect(document.documentElement.classList.contains("dark")).toBe(true);
  system.matches = false;
  system.dispatchEvent(new Event("change"));
  expect(document.documentElement.classList.contains("dark")).toBe(false);
  fireEvent.click(view.getByRole("button"));
  expect(document.documentElement.classList.contains("dark")).toBe(true);
  expect(localStorage.getItem("devfeed:theme")).toBe("dark");
  system.dispatchEvent(new Event("change"));
  expect(document.documentElement.classList.contains("dark")).toBe(true);
  view.unmount();
  render(<ThemeToggle />);
  expect(document.documentElement.classList.contains("dark")).toBe(true);
});

it("synchronizes other tabs and returns to system preference when storage is cleared", () => {
  render(<ThemeToggle />);
  window.dispatchEvent(
    new StorageEvent("storage", { key: "devfeed:theme", newValue: "light" }),
  );
  expect(document.documentElement.classList.contains("dark")).toBe(false);
  window.dispatchEvent(new StorageEvent("storage", { key: null }));
  expect(document.documentElement.classList.contains("dark")).toBe(true);
});

it("still initializes and toggles when storage is blocked", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("Blocked");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("Blocked");
  });
  new Function(themeScript)();
  const view = render(<ThemeToggle />);
  expect(document.documentElement.classList.contains("dark")).toBe(true);
  fireEvent.click(view.getByRole("button"));
  expect(document.documentElement.classList.contains("dark")).toBe(false);
  system.dispatchEvent(new Event("change"));
  expect(document.documentElement.classList.contains("dark")).toBe(false);
});
