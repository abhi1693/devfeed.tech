// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FirstVisitOnboarding } from "@/components/first-visit-onboarding";

vi.mock("@/components/user-account", () => ({
  useUser: () => ({ user: null, loading: false, unavailable: false }),
}));

beforeEach(() => {
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.setAttribute("open", "");
    },
  });
  Object.defineProperty(HTMLDialogElement.prototype, "close", {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.removeAttribute("open");
    },
  });
  localStorage.clear();
  window.history.pushState({}, "", "/latest");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("introduces first-time visitors to DevFeed without asking them to sign up", async () => {
  const user = userEvent.setup();
  render(<FirstVisitOnboarding />);

  expect(
    await screen.findByRole("dialog", {
      name: "DevFeed is your daily briefing on what’s next.",
    }),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("dialog"), { clientX: -10, clientY: -10 });
  expect(screen.getByRole("dialog")).toBeTruthy();
  expect(screen.getByText(/developer news, launches, tutorials/)).toBeTruthy();
  expect(screen.queryByRole("link", { name: /install/i })).toBeNull();

  await user.click(screen.getByRole("button", { name: /next/i }));
  expect(
    screen.getByRole("heading", { name: "Discover what to learn and build next." }),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: /next/i }));
  await user.click(screen.getByRole("button", { name: /next/i }));
  expect(
    screen.getByRole("heading", { name: "Turn one good read into your next move." }),
  ).toBeTruthy();
  await user.click(screen.getByRole("link", { name: /start reading/i }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(localStorage.getItem("devfeed:first-visit-onboarding-seen")).toBe("1");
});
