// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SignupNudge } from "@/components/signup-nudge";
import { anonymousInvitationDismissedKey } from "@/components/reader-prompts";

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
  vi.restoreAllMocks();
  window.history.replaceState({}, "", "/");
});

function qualify() {
  sessionStorage.setItem("devfeed:signup-nudge-articles", JSON.stringify(["one", "two", "three"]));
}

it("remembers keyboard dismissal across routes and remounts for the session", async () => {
  qualify();
  const reader = view("/latest");
  const keyboard = userEvent.setup();
  screen.getByRole("button", { name: "Not now" }).focus();
  await keyboard.keyboard("{Enter}");
  expect(screen.queryByRole("complementary", { name: "Create a DevFeed account" })).toBeNull();
  expect(sessionStorage.getItem(anonymousInvitationDismissedKey)).toBe("true");
  reader.rerender(<SignupNudge pathname="/articles/four" />);
  expect(screen.queryByRole("button", { name: "Not now" })).toBeNull();
  reader.unmount();
  view("/latest");
  expect(screen.queryByRole("button", { name: "Not now" })).toBeNull();
});

it("respects existing anonymous Dev Card dismissal", () => {
  qualify();
  sessionStorage.setItem(anonymousInvitationDismissedKey, "true");
  view("/latest");
  expect(screen.queryByRole("link", { name: "Create account" })).toBeNull();
});

it("does not apply another account's Dev Card dismissal to a guest", () => {
  qualify();
  sessionStorage.setItem(`${anonymousInvitationDismissedKey}:other-reader`, "true");
  view("/latest");
  expect(screen.getByRole("link", { name: "Create account" })).toBeTruthy();
});

it.each(["loading", "unavailable"] as const)(
  "stays hidden while authentication is %s",
  (status) => {
    qualify();
    userState[status] = true;
    view("/articles/four");
    expect(screen.queryByRole("link", { name: "Create account" })).toBeNull();
    expect(JSON.parse(sessionStorage.getItem("devfeed:signup-nudge-articles")!)).toHaveLength(3);
  },
);

it("counts distinct valid article routes and ignores malformed stored values", () => {
  sessionStorage.setItem("devfeed:signup-nudge-articles", JSON.stringify([null, {}, "bad/slug"]));
  const reader = view("/articles/one");
  reader.rerender(<SignupNudge pathname="/latest" />);
  reader.rerender(<SignupNudge pathname="/articles/one" />);
  reader.rerender(<SignupNudge pathname="/articles/two" />);
  expect(screen.queryByRole("link", { name: "Create account" })).toBeNull();
  reader.rerender(<SignupNudge pathname="/articles/three" />);
  expect(screen.getByRole("link", { name: "Create account" })).toBeTruthy();
});

it("retains article progress and dismissal in memory when storage is blocked", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  const reader = view("/articles/one");
  reader.rerender(<SignupNudge pathname="/articles/two" />);
  reader.rerender(<SignupNudge pathname="/articles/three" />);
  fireEvent.click(screen.getByRole("button", { name: "Not now" }));
  reader.rerender(<SignupNudge pathname="/articles/four" />);
  expect(screen.queryByRole("link", { name: "Create account" })).toBeNull();
});

it("keeps the current article and filters in the sign-in return link", () => {
  qualify();
  window.history.replaceState({}, "", "/articles/three?topic=python&sort=latest");
  view("/articles/three");
  const href = screen.getByRole("link", { name: "Create account" }).getAttribute("href")!;
  expect(new URL(href, window.location.origin).searchParams.get("return_to")).toBe(
    "/articles/three?topic=python&sort=latest",
  );
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
