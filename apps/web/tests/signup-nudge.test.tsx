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
const readerState = vi.hoisted(() => ({
  currentArticle: null as { id: string; slug: string } | null,
  promptEligibility: { signup: true, mustReads: true },
}));

vi.mock("@/components/user-account", () => ({
  useUser: () => userState,
}));
vi.mock("@/components/reader-context", () => ({
  useReaderContext: () => readerState,
}));

beforeEach(() => {
  sessionStorage.clear();
  userState.user = null;
  userState.loading = false;
  userState.unavailable = false;
  readerState.currentArticle = null;
  readerState.promptEligibility = { signup: true, mustReads: true };
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.history.replaceState({}, "", "/");
});

function qualify() {
  sessionStorage.setItem("devfeed:signup-nudge-articles", JSON.stringify(["one", "two", "three"]));
}

function view(slug?: string) {
  readerState.currentArticle = slug ? { id: slug, slug } : null;
  return render(<SignupNudge />);
}

function visit(reader: ReturnType<typeof render>, slug?: string) {
  readerState.currentArticle = slug ? { id: slug, slug } : null;
  reader.rerender(<SignupNudge />);
}

it("remembers keyboard dismissal across routes and remounts for the session", async () => {
  qualify();
  const reader = view();
  const keyboard = userEvent.setup();
  screen.getByRole("button", { name: "Not now" }).focus();
  await keyboard.keyboard("{Enter}");
  expect(screen.queryByRole("complementary", { name: "Create a DevFeed account" })).toBeNull();
  expect(sessionStorage.getItem(anonymousInvitationDismissedKey)).toBe("true");
  visit(reader, "four");
  expect(screen.queryByRole("button", { name: "Not now" })).toBeNull();
  reader.unmount();
  view();
  expect(screen.queryByRole("button", { name: "Not now" })).toBeNull();
});

it("respects existing anonymous Dev Card dismissal", () => {
  qualify();
  sessionStorage.setItem(anonymousInvitationDismissedKey, "true");
  view();
  expect(screen.queryByRole("link", { name: "Create account" })).toBeNull();
});

it("does not apply another account's Dev Card dismissal to a guest", () => {
  qualify();
  sessionStorage.setItem(`${anonymousInvitationDismissedKey}:other-reader`, "true");
  view();
  expect(screen.getByRole("link", { name: "Create account" })).toBeTruthy();
});

it.each(["loading", "unavailable"] as const)(
  "stays hidden while authentication is %s",
  (status) => {
    qualify();
    userState[status] = true;
    view("four");
    expect(screen.queryByRole("link", { name: "Create account" })).toBeNull();
    expect(JSON.parse(sessionStorage.getItem("devfeed:signup-nudge-articles")!)).toHaveLength(3);
  },
);

it("counts distinct loaded articles and ignores malformed stored values", () => {
  sessionStorage.setItem("devfeed:signup-nudge-articles", JSON.stringify([null, {}, "bad/slug"]));
  const reader = view("one");
  visit(reader);
  visit(reader, "one");
  visit(reader, "two");
  expect(screen.queryByRole("link", { name: "Create account" })).toBeNull();
  visit(reader, "three");
  expect(screen.getByRole("link", { name: "Create account" })).toBeTruthy();
  expect(JSON.parse(sessionStorage.getItem("devfeed:signup-nudge-articles")!)).toEqual([
    "one",
    "two",
    "three",
  ]);
});

it("does not count article-shaped URLs without a current article", () => {
  window.history.replaceState({}, "", "/articles/missing");
  const reader = view();
  expect(sessionStorage.getItem("devfeed:signup-nudge-articles")).toBeNull();
  window.history.replaceState({}, "", "/articles/still-loading");
  reader.rerender(<SignupNudge />);
  expect(sessionStorage.getItem("devfeed:signup-nudge-articles")).toBeNull();
});

it("counts the semantic current article independently of the transport URL", () => {
  window.history.replaceState({}, "", "/search?preview=one");
  const reader = view("one");
  visit(reader, "two");
  visit(reader, "three");
  expect(screen.getByRole("link", { name: "Create account" })).toBeTruthy();
  expect(JSON.parse(sessionStorage.getItem("devfeed:signup-nudge-articles")!)).toEqual([
    "one",
    "two",
    "three",
  ]);
});

it("retains article progress and dismissal in memory when storage is blocked", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  const reader = view("one");
  visit(reader, "two");
  visit(reader, "three");
  fireEvent.click(screen.getByRole("button", { name: "Not now" }));
  visit(reader, "four");
  expect(screen.queryByRole("link", { name: "Create account" })).toBeNull();
});

it("keeps approved filters in the sign-in return link", () => {
  qualify();
  window.history.replaceState({}, "", "/latest?topic=python&sort=newest");
  view("three");
  const href = screen.getByRole("link", { name: "Create account" }).getAttribute("href")!;
  expect(new URL(href, window.location.origin).searchParams.get("return_to")).toBe(
    "/latest?topic=python&sort=newest",
  );
});

it.each(["click", "keyboard", "auxclick", "contextmenu", "control", "meta"] as const)(
  "refreshes the sign-in destination on %s without requiring a context render",
  async (activation) => {
    qualify();
    window.history.replaceState({}, "", "/latest?topic=typescript");
    view();
    const link = screen.getByRole("link", { name: "Create account" });
    const initialHref = link.getAttribute("href");
    window.history.replaceState({}, "", "/search?q=python&section=sources");
    expect(link.getAttribute("href")).toBe(initialHref);
    // Keep jsdom on the current page while the component prepares native navigation.
    link.addEventListener("click", (event) => event.preventDefault());
    if (activation === "keyboard") {
      link.focus();
      await userEvent.setup().keyboard("{Enter}");
    } else if (activation === "auxclick") {
      fireEvent(link, new MouseEvent("auxclick", { bubbles: true, button: 1 }));
    } else if (activation === "contextmenu") {
      fireEvent.contextMenu(link);
    } else {
      fireEvent.click(link, {
        ctrlKey: activation === "control",
        metaKey: activation === "meta",
      });
    }
    expect(
      new URL(link.getAttribute("href")!, window.location.origin).searchParams.get("return_to"),
    ).toBe("/search?q=python&section=sources");
  },
);

it("appears after three distinct public articles", () => {
  const reader = view("one");
  visit(reader, "two");
  visit(reader, "three");
  expect(screen.getByRole("complementary", { name: "Create a DevFeed account" })).toBeTruthy();
  expect(screen.getByText("Create your DevFeed account.")).toBeTruthy();
});

it("does not show for signed-in readers", () => {
  userState.user = { user_id: "reader" };
  const reader = view("one");
  visit(reader, "two");
  visit(reader, "three");
  expect(screen.queryByRole("complementary", { name: "Create a DevFeed account" })).toBeNull();
  expect(sessionStorage.getItem("devfeed:signup-nudge-articles")).toBeNull();
});

it.each(["/login", "/register", "/extension/login-complete", "/missing", "/latest"])(
  "obeys prompt eligibility rather than recognizing %s",
  (pathname) => {
    qualify();
    const reader = view();
    expect(screen.getByRole("link", { name: "Create account" })).toBeTruthy();

    window.history.replaceState({}, "", pathname);
    readerState.promptEligibility.signup = false;
    reader.rerender(<SignupNudge />);
    expect(screen.queryByRole("complementary", { name: "Create a DevFeed account" })).toBeNull();

    readerState.promptEligibility.signup = true;
    reader.rerender(<SignupNudge />);
    expect(screen.getByRole("link", { name: "Create account" }).getAttribute("href")).toBe(
      `/login?return_to=${encodeURIComponent(["/extension/login-complete", "/latest"].includes(pathname) ? pathname : "/")}`,
    );
    expect(sessionStorage.getItem(anonymousInvitationDismissedKey)).toBeNull();
  },
);
