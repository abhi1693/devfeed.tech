// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppearanceSettings } from "@/components/appearance-settings";
import { ThemeToggle } from "@/components/theme-toggle";
import { ThemePreferencesProvider } from "@/components/theme-preferences";
import { UserProvider } from "@/components/user-account";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); document.documentElement.classList.remove("dark"); });
it("loads account theme and synchronizes the settings dropdown and navbar cycle after saving", async () => {
  let theme = "dark"; let fail = false;
  const fetcher = vi.fn((url: string, init?: RequestInit) => {
    if (url.endsWith("auth/me")) return Promise.resolve(Response.json({ user_id: "user-a", csrf_token: "csrf" }));
    if (init?.method === "PUT") {
      if (fail) return Promise.resolve(Response.json({}, { status: 503 }));
      theme = JSON.parse(String(init.body)).theme;
    }
    return Promise.resolve(Response.json({ theme }));
  });
  vi.stubGlobal("fetch", fetcher);
  render(<UserProvider><ThemePreferencesProvider><ThemeToggle /><AppearanceSettings /></ThemePreferencesProvider></UserProvider>);
  const dropdown = await screen.findByRole("combobox", { name: "Theme" });
  await waitFor(() => expect(document.documentElement.classList.contains("dark")).toBe(true));
  fireEvent.click(screen.getByRole("button", { name: "Theme: Dark. Switch to system theme" }));
  await waitFor(() => expect(theme).toBe("system"));
  await screen.findByRole("button", { name: "Theme: System. Switch to light theme" });
  expect(dropdown.textContent).toContain("System");
  fireEvent.click(dropdown); fireEvent.click(screen.getByRole("option", { name: "Light" }));
  await screen.findByRole("button", { name: "Theme: Light. Switch to dark theme" });
  expect(localStorage.getItem("devfeed:theme")).toBe("light");
  const write = fetcher.mock.calls.find(([, init]) => init?.method === "PUT")?.[1];
  expect(write?.headers).toEqual({ "Content-Type": "application/json", "X-CSRF-Token": "csrf" });
  fail = true;
  fireEvent.click(screen.getByRole("button", { name: "Theme: Light. Switch to dark theme" }));
  await screen.findAllByRole("alert");
  expect(theme).toBe("light");
  expect(document.documentElement.classList.contains("dark")).toBe(false);
});
