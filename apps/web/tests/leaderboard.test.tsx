// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Leaderboard } from "@/components/leaderboard";
import type { UserProfile } from "@/lib/user";

const state = vi.hoisted(() => ({
  user: null as { user_id: string } | null,
  loading: false,
  profile: null as UserProfile | null,
  profileUnavailable: false,
  sessionRevision: 1,
}));
vi.mock("@/components/user-account", () => ({ useUser: () => state }));
const reader = { rank: 1, username: "reader", display_name: "Reader", avatar_url: null, days: 21 };
const publicBoards = { longest_streak: [reader], reading_days: [{ ...reader, days: 63 }] };
const ownRanks = {
  longest_streak: { ...reader, rank: 15, username: "mine", days: 4 },
  reading_days: { ...reader, rank: 12, username: "mine", days: 14 },
};
const network = vi.fn();
beforeEach(() => {
  state.user = null;
  state.loading = false;
  state.profile = null;
  state.profileUnavailable = false;
  network
    .mockReset()
    .mockImplementation((url: string) =>
      Promise.resolve(Response.json(url.endsWith("/me") ? ownRanks : publicBoards)),
    );
  vi.stubGlobal("fetch", network);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows public boards without requesting personal data and links ranked profiles", async () => {
  render(<Leaderboard />);
  const board = await screen.findByRole("region", { name: "Longest streak" });
  expect(
    within(board).getByRole("link", { name: "Reader, rank 1, 21 days" }).getAttribute("href"),
  ).toBe("/users/reader");
  expect(screen.getAllByRole("link", { name: "Sign in to join" })).toHaveLength(2);
  expect(network).toHaveBeenCalledTimes(1);
  expect(network).toHaveBeenCalledWith(
    "/api/v1/leaderboard",
    expect.objectContaining({ credentials: "omit", cache: "no-store" }),
  );
});

it("shows the signed-in reader's global rank even outside the top ten and clears it on sign-out", async () => {
  state.user = { user_id: "mine" };
  const view = render(<Leaderboard />);
  expect(await screen.findByRole("link", { name: "Reader, rank 15, 4 days, you" })).toBeTruthy();
  expect(screen.getByRole("link", { name: "Reader, rank 12, 14 days, you" })).toBeTruthy();
  expect(network).toHaveBeenCalledWith(
    "/api/v1/user/leaderboard/me",
    expect.objectContaining({ credentials: "same-origin" }),
  );
  state.user = null;
  view.rerender(<Leaderboard />);
  expect(screen.queryByRole("link", { name: "Reader, rank 15, 4 days, you" })).toBeNull();
});

it("keeps the public rankings when personal ranks fail and lets the reader retry", async () => {
  state.user = { user_id: "mine" };
  network.mockImplementation((url: string) =>
    Promise.resolve(
      url.endsWith("/me") ? new Response(null, { status: 503 }) : Response.json(publicBoards),
    ),
  );
  render(<Leaderboard />);
  expect(await screen.findAllByRole("button", { name: "Retry your ranking" })).toHaveLength(2);
  expect(screen.getByRole("link", { name: "Reader, rank 1, 21 days" })).toBeTruthy();
  network.mockImplementation((url: string) =>
    Promise.resolve(Response.json(url.endsWith("/me") ? ownRanks : publicBoards)),
  );
  fireEvent.click(screen.getAllByRole("button", { name: "Retry your ranking" })[0]);
  expect(await screen.findByRole("link", { name: "Reader, rank 15, 4 days, you" })).toBeTruthy();
});

it("recovers a failed public read and distinguishes empty rankings from a failure", async () => {
  network.mockResolvedValue(new Response(null, { status: 503 }));
  render(<Leaderboard />);
  expect(await screen.findByRole("alert")).toBeTruthy();
  network.mockResolvedValue(Response.json({ longest_streak: [], reading_days: [] }));
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findAllByText(/No rankings yet/)).toHaveLength(2);
  expect(screen.queryByRole("alert")).toBeNull();
});

it.each([
  { username: null, published: false, action: "Claim your username" },
  { username: "mine", published: false, action: "Make your profile public" },
  { username: "mine", published: true, action: "Start reading" },
])("offers $action for an unranked reader", async ({ username, published, action }) => {
  state.user = { user_id: "mine" };
  state.profile = {
    display_name: "Reader",
    avatar_url: null,
    username,
    visibility: {
      public: published,
      location: false,
      stack: false,
      heatmap: false,
      achievements: false,
    },
  };
  network.mockImplementation((url: string) =>
    Promise.resolve(
      Response.json(
        url.endsWith("/me") ? { longest_streak: null, reading_days: null } : publicBoards,
      ),
    ),
  );
  render(<Leaderboard />);
  expect(await screen.findAllByRole("link", { name: action })).toHaveLength(2);
});

it("does not show a previous account's rank while the next account loads", async () => {
  state.user = { user_id: "mine" };
  const view = render(<Leaderboard />);
  await screen.findByRole("link", { name: "Reader, rank 15, 4 days, you" });
  let finish!: (response: Response) => void;
  network.mockImplementation(
    () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  );
  state.user = { user_id: "other" };
  view.rerender(<Leaderboard />);
  expect(screen.queryByRole("link", { name: "Reader, rank 15, 4 days, you" })).toBeNull();
  await act(async () => finish(Response.json({ longest_streak: null, reading_days: null })));
});
