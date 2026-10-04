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

it.each([1, 10])(
  "shows a listed reader once with %i public rows and no personal footer",
  async (size) => {
    state.user = { user_id: "mine" };
    const entries = Array.from({ length: size }, (_, index) => ({
      ...reader,
      rank: index + 1,
      username: `reader-${index}`,
    }));
    const mine = entries[size - 1];
    network.mockImplementation((url: string) =>
      Promise.resolve(
        Response.json(
          url.endsWith("/me")
            ? { longest_streak: mine, reading_days: mine }
            : { longest_streak: entries, reading_days: entries },
        ),
      ),
    );
    render(<Leaderboard />);
    expect(
      await screen.findAllByRole("link", { name: `Reader, rank ${size}, 21 days, you` }),
    ).toHaveLength(2);
    for (const title of ["Longest streak", "Most reading days"]) {
      const board = screen.getByRole("region", { name: title });
      expect(within(board).getAllByRole("listitem")).toHaveLength(size);
      expect(within(board).getAllByRole("link", { name: /, you$/ })).toHaveLength(1);
      expect(within(board).queryByLabelText(`Your ${title.toLowerCase()} ranking`)).toBeNull();
    }
  },
);

it.each([
  { key: "longest_streak", title: "Longest streak", otherTitle: "Most reading days" },
  { key: "reading_days", title: "Most reading days", otherTitle: "Longest streak" },
] as const)(
  "checks whether the reader is listed independently for $title",
  async ({ key, title, otherTitle }) => {
    state.user = { user_id: "mine" };
    network.mockImplementation((url: string) =>
      Promise.resolve(
        Response.json(
          url.endsWith("/me") ? { ...ownRanks, [key]: publicBoards[key][0] } : publicBoards,
        ),
      ),
    );
    render(<Leaderboard />);
    const board = await screen.findByRole("region", { name: title });
    await within(board).findByRole("link", { name: /, you$/ });
    expect(within(board).getAllByRole("link", { name: /, you$/ })).toHaveLength(1);
    expect(within(board).queryByLabelText(`Your ${title.toLowerCase()} ranking`)).toBeNull();
    const other = screen.getByRole("region", { name: otherTitle });
    expect(
      within(within(other).getByLabelText(`Your ${otherTitle.toLowerCase()} ranking`)).getByRole(
        "link",
        { name: /, you$/ },
      ),
    ).toBeTruthy();
  },
);

it("keeps the personal footer for a tied rank whose username is outside the displayed ten", async () => {
  state.user = { user_id: "mine" };
  const entries = Array.from({ length: 10 }, (_, index) => ({
    ...reader,
    username: `reader-${index}`,
  }));
  const mine = { ...reader, username: "mine" };
  network.mockImplementation((url: string) =>
    Promise.resolve(
      Response.json(
        url.endsWith("/me")
          ? { longest_streak: mine, reading_days: mine }
          : { longest_streak: entries, reading_days: entries },
      ),
    ),
  );
  render(<Leaderboard />);
  expect(await screen.findAllByRole("link", { name: "Reader, rank 1, 21 days, you" })).toHaveLength(
    2,
  );
  for (const title of ["Longest streak", "Most reading days"]) {
    const board = screen.getByRole("region", { name: title });
    expect(within(board).getAllByRole("listitem")).toHaveLength(10);
    expect(
      within(within(board).getByLabelText(`Your ${title.toLowerCase()} ranking`)).getByRole(
        "link",
        { name: "Reader, rank 1, 21 days, you" },
      ),
    ).toBeTruthy();
  }
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
