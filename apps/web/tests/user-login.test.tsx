// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { UserLogin } from "@/components/user-login";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
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
