// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { toast } from "sonner";
import { Toaster } from "@/components/atoms/sonner";
import { notify } from "@/lib/notifications";
import { StrictMode } from "react";
import { LoginFeedback } from "@/components/molecules/login-feedback";
import ErrorPage from "@/app/error";

beforeEach(() => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
});
afterEach(async () => { await act(async () => { toast.dismiss(); }); cleanup(); vi.unstubAllGlobals(); });

describe("shared toast host", () => {
  it("does not lose initial login feedback before the host subscribes", async () => {
    render(<StrictMode><LoginFeedback error="Required role missing." signedOut={false} /><Toaster /></StrictMode>);
    await screen.findByText("Sign-in failed");
    expect(screen.getAllByText("Required role missing.")).toHaveLength(1);
  });
  it("notifies initial page errors while preserving the recovery screen", async () => {
    render(<StrictMode><ErrorPage reset={vi.fn()} /><Toaster /></StrictMode>);
    await screen.findByText("Could not load this page");
    expect(screen.getByRole("button", { name: "Try again" })).toBeDefined();
  });
  it.each(["success", "error", "warning", "info"] as const)("renders accessible %s feedback with a dismiss button", async severity => {
    render(<Toaster />);
    act(() => { notify[severity](`${severity} result`, { description: "Helpful details." }); });
    await screen.findByText(`${severity} result`);
    expect(document.querySelector(`[data-sonner-toast][data-type="${severity}"]`)).not.toBeNull();
    expect(screen.getByRole("region", { name: /Notifications/ }).getAttribute("aria-live")).toBe("polite");
    expect(screen.getByText("Helpful details.")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Close toast" }));
    await waitFor(() => expect(screen.queryByText(`${severity} result`)).toBeNull());
  });
  it("survives page content changes without duplicating a repeated notification ID", async () => {
    const view = render(<><main>Form page</main><Toaster /></>);
    act(() => { notify.success("Source saved", { id: "save" }); });
    await screen.findByText("Source saved");
    view.rerender(<><main>Detail page</main><Toaster /></>);
    act(() => { notify.success("Source saved", { id: "save" }); });
    await waitFor(() => expect(screen.getAllByText("Source saved")).toHaveLength(1));
    expect(document.querySelectorAll("[data-sonner-toaster]")).toHaveLength(1);
  });
  it("renders API text as text, never HTML", async () => {
    render(<Toaster />);
    act(() => { notify.error("Could not save", { description: '<img src=x onerror="alert(1)">' }); });
    await screen.findByText('<img src=x onerror="alert(1)">');
    expect(document.querySelector("img")).toBeNull();
  });
});
