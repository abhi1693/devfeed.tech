// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SignupNudge } from "@/components/signup-nudge";

const userState = vi.hoisted(() => ({
  user: null as { user_id: string } | null,
  loading: false,
  unavailable: false,
}));

vi.mock("@/components/user-account", () => ({
  useUser: () => userState,
}));

beforeEach(() => {
  sessionStorage.clear();
  userState.user = null;
  userState.loading = false;
  userState.unavailable = false;
});

afterEach(() => {
  cleanup();
  window.history.replaceState({}, "", "/");
});

function view(pathname: string) {
  return render(<SignupNudge pathname={pathname} />);
}

it("appears after three distinct public articles", () => {
  const viewOne = view("/articles/one");
  viewOne.rerender(<SignupNudge pathname="/articles/two" />);
  viewOne.rerender(<SignupNudge pathname="/articles/three" />);
  expect(screen.getByRole("complementary", { name: "Create a DevFeed account" })).toBeTruthy();
  expect(screen.getByText("Create your DevFeed account.")).toBeTruthy();
});

it("does not show for signed-in readers", () => {
  userState.user = { user_id: "reader" };
  const viewOne = view("/articles/one");
  viewOne.rerender(<SignupNudge pathname="/articles/two" />);
  viewOne.rerender(<SignupNudge pathname="/articles/three" />);
  expect(screen.queryByRole("complementary", { name: "Create a DevFeed account" })).toBeNull();
});

it.each(["/login", "/login/", "/register", "/extension/login-complete"])(
  "hides the nudge on %s after the article threshold is reached",
  (pathname) => {
    const reader = view("/articles/one");
    reader.rerender(<SignupNudge pathname="/articles/two" />);
    reader.rerender(<SignupNudge pathname="/articles/three" />);
    expect(screen.getByRole("link", { name: "Create account" })).toBeTruthy();

    window.history.replaceState({}, "", `${pathname}?return_to=%2Farticles%2Fthree`);
    reader.rerender(<SignupNudge pathname={pathname} />);
    expect(screen.queryByRole("complementary", { name: "Create a DevFeed account" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Create account" })).toBeNull();

    window.history.replaceState({}, "", "/articles/three");
    reader.rerender(<SignupNudge pathname="/articles/three" />);
    expect(screen.getByRole("link", { name: "Create account" }).getAttribute("href")).toBe(
      "/login?return_to=%2Farticles%2Fthree",
    );
  },
);
