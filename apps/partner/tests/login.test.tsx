// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { LoginPanel } from "../src/app/login/login-panel";

afterEach(cleanup);

describe("partner sign-in", () => {
  it("uses configured providers with partner-only login routes", () => {
    render(<LoginPanel config={{ enabled: true, providers: ["github", "google"] }} />);
    expect(screen.getByRole("heading", { name: "Sign in to DevFeed Partners" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "GitHub" }).getAttribute("href")).toBe(
      "/api/v1/partner/auth/login?provider=github",
    );
    expect(screen.getByRole("link", { name: "Google" }).getAttribute("href")).toBe(
      "/api/v1/partner/auth/login?provider=google",
    );
  });

  it("requests fresh authentication after denied access", () => {
    render(<LoginPanel config={{ enabled: true, providers: [] }} error="Access denied" />);
    expect(screen.getByRole("alert").textContent).toBe("Access denied");
    expect(screen.getByRole("link", { name: "Sign in again" }).getAttribute("href")).toBe(
      "/api/v1/partner/auth/login?reauthenticate=true",
    );
  });

  it("does not offer sign-in while the service is unavailable", () => {
    render(<LoginPanel config={null} unavailable />);
    expect(screen.queryByRole("link", { name: "Continue to sign in" })).toBeNull();
    expect(screen.getByText(/Partner sign-in is not available/)).toBeTruthy();
  });
});
