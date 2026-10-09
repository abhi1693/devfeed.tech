// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ReaderPromptsProvider, promptPauseMs } from "@/components/reader-prompts";
import { SignupNudge } from "@/components/signup-nudge";
import { DevCardPromo } from "@/components/dev-card-promo";
import { FeedOnboarding } from "@/components/feed-onboarding";
import type { UserProfile } from "@/lib/user";

const state = vi.hoisted(() => ({
  user: null as { user_id: string } | null,
  loading: false,
  unavailable: false,
  profile: null as UserProfile | null,
  profileUnavailable: false,
}));
const follows = vi.hoisted(() => ({
  ids: [] as string[],
  loading: false,
  unavailable: false,
  refresh: vi.fn(),
  save: vi.fn(),
}));
vi.mock("@/components/user-account", () => ({ useUser: () => state }));
vi.mock("@/components/topic-follows", () => ({ useTopicFollows: () => follows }));
vi.mock("@/components/infinite-choices", () => ({ InfiniteChoices: () => null }));
vi.mock("@/components/dev-card-artwork", () => ({ DevCardArtwork: () => <div>Card artwork</div> }));

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(performance, "now").mockReturnValue(30_000);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        profile: { username: "asaharan", display_name: "Featured Reader", stack: [] },
      }),
    ),
  );
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
  sessionStorage.clear();
  localStorage.clear();
  sessionStorage.setItem("devfeed:signup-nudge-articles", JSON.stringify(["one", "two", "three"]));
  state.user = null;
  state.loading = false;
  state.unavailable = false;
  state.profile = null;
  state.profileUnavailable = false;
  follows.ids = [];
  follows.loading = false;
  follows.unavailable = false;
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  expect(document.body.style.overflow).toBe("");
});

async function advance(ms = 1500) {
  await act(() => vi.advanceTimersByTimeAsync(ms));
}
function invites({ signup = true, requested = false, onboarding = false } = {}) {
  return (
    <ReaderPromptsProvider>
      {onboarding && <FeedOnboarding />}
      <DevCardPromo requested={requested} />
      {signup && <SignupNudge pathname="/latest" />}
    </ReaderPromptsProvider>
  );
}

it.each([false, true])(
  "lets the banner finish and silences automatic Dev Card invitations on dismissal (strict=%s)",
  async (strict) => {
    render(strict ? <StrictMode>{invites()}</StrictMode> : invites());
    await advance();
    expect(screen.getByRole("button", { name: "Not now" })).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "Your dev card preview" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    await advance(40_000);
    expect(screen.queryByRole("link", { name: "Create account" })).toBeNull();
    expect(screen.queryByRole("dialog", { name: "Your dev card preview" })).toBeNull();
  },
);

it("silences signup after an anonymous Dev Card dismissal", async () => {
  const reader = render(invites({ signup: false }));
  await advance();
  await advance(1200);
  fireEvent.click(screen.getByRole("button", { name: "Dismiss dev card preview" }));
  reader.rerender(invites());
  await advance();
  expect(screen.queryByRole("link", { name: "Create account" })).toBeNull();
});

it("prioritizes topic onboarding, including preference loading, and pauses after closing it", async () => {
  state.user = { user_id: "reader" };
  state.profile = { display_name: "Reader", avatar_url: null, username: null };
  follows.loading = true;
  const reader = render(invites({ onboarding: true }));
  await advance(40_000);
  expect(screen.queryByRole("dialog")).toBeNull();
  follows.loading = false;
  reader.rerender(invites({ onboarding: true }));
  await advance();
  expect(screen.getByRole("dialog", { name: "Choose your topics" })).toBeTruthy();
  expect(screen.queryByRole("dialog", { name: "Your dev card preview" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  await advance(promptPauseMs - 1);
  expect(screen.queryByRole("dialog")).toBeNull();
  await advance(251);
  expect(screen.getByRole("dialog", { name: "Your dev card preview" })).toBeTruthy();
});

it("allows an explicitly requested preview after dismissing signup", async () => {
  const reader = render(invites());
  await advance();
  fireEvent.click(screen.getByRole("button", { name: "Not now" }));
  reader.rerender(invites({ requested: true }));
  await advance();
  expect(screen.getByRole("dialog", { name: "Your dev card preview" })).toBeTruthy();
  expect(screen.queryByRole("link", { name: "Create account" })).toBeNull();
});

it("gives an explicit preview priority over a visible signup banner", async () => {
  const reader = render(invites());
  await advance();
  expect(screen.getByRole("button", { name: "Not now" })).toBeTruthy();
  reader.rerender(invites({ requested: true }));
  await advance();
  expect(screen.getByRole("dialog", { name: "Your dev card preview" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Not now" })).toBeNull();
});

it("hides signup while a native article dialog is open and resumes after it closes", async () => {
  render(
    <ReaderPromptsProvider>
      <SignupNudge pathname="/latest" />
    </ReaderPromptsProvider>,
  );
  await advance();
  const article = document.createElement("dialog");
  await act(async () => {
    document.body.append(article);
    article.showModal();
  });
  expect(screen.queryByRole("button", { name: "Not now" })).toBeNull();
  await act(async () => {
    article.close();
    article.remove();
  });
  expect(screen.getByRole("button", { name: "Not now" })).toBeTruthy();
});

it("retains refusal when storage writes fail and invites remount under the same reader", async () => {
  const reader = render(invites());
  await advance();
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  fireEvent.click(screen.getByRole("button", { name: "Not now" }));
  reader.rerender(
    <ReaderPromptsProvider>
      <div>Reader route</div>
    </ReaderPromptsProvider>,
  );
  reader.rerender(invites());
  await advance(40_000);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByRole("button", { name: "Not now" })).toBeNull();
});

it("keeps signed-in Dev Card completion separate from a guest refusal", async () => {
  const reader = render(invites());
  await advance();
  fireEvent.click(screen.getByRole("button", { name: "Not now" }));
  state.user = { user_id: "reader" };
  state.profile = { display_name: "Reader", avatar_url: null, username: null };
  reader.rerender(invites({ signup: false }));
  await advance();
  expect(screen.getByRole("link", { name: /Finish your dev card/ })).toBeTruthy();
});
