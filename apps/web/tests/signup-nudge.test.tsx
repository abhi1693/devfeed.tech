// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
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
  document.body.innerHTML = "";
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
