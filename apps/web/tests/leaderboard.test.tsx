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
const reader = {
  position: 1,
  rank: 1,
  username: "reader",
  display_name: "Reader",
  avatar_url: null,
  days: 21,
};
const publicBoards = { longest_streak: [reader], reading_days: [{ ...reader, days: 63 }] };
const ownRanks = {
  longest_streak: { ...reader, position: 15, rank: 15, username: "mine", days: 4 },
  reading_days: { ...reader, position: 12, rank: 12, username: "mine", days: 14 },
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
    within(board)
      .getByRole("link", { name: rankingName("Reader", 1, 21, false) })
      .getAttribute("href"),
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
  expect(
    await screen.findByRole("link", { name: rankingName("Reader", 15, 4, true) }),
  ).toBeTruthy();
  expect(screen.getByRole("link", { name: rankingName("Reader", 12, 14, true) })).toBeTruthy();
  expect(network).toHaveBeenCalledWith(
    "/api/v1/user/leaderboard/me",
    expect.objectContaining({ credentials: "same-origin" }),
  );
  state.user = null;
  view.rerender(<Leaderboard />);
  expect(screen.queryByRole("link", { name: rankingName("Reader", 15, 4, true) })).toBeNull();
});

it.each([1, 10])(
  "shows a listed reader once with %i public rows and no personal footer",
  async (size) => {
    state.user = { user_id: "mine" };
    const entries = Array.from({ length: size }, (_, index) => ({
      ...reader,
      rank: index + 1,
      position: index + 1,
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
      await screen.findAllByRole("link", { name: rankingName("Reader", size, 21, true) }),
    ).toHaveLength(2);
    for (const title of ["Longest streak", "Most reading days"]) {
      const board = screen.getByRole("region", { name: title });
      expect(within(board).getAllByRole("listitem")).toHaveLength(size);
      expect(within(board).getAllByRole("link", { name: /You.*@/ })).toHaveLength(1);
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
    await within(board).findByRole("link", { name: /You.*@/ });
    expect(within(board).getAllByRole("link", { name: /You.*@/ })).toHaveLength(1);
    expect(within(board).queryByLabelText(`Your ${title.toLowerCase()} ranking`)).toBeNull();
    const other = screen.getByRole("region", { name: otherTitle });
    expect(
      within(within(other).getByLabelText(`Your ${otherTitle.toLowerCase()} ranking`)).getByRole(
        "link",
        { name: /You.*@/ },
      ),
    ).toBeTruthy();
  },
);

it("keeps the personal footer for a tied rank whose username is outside the displayed ten", async () => {
  state.user = { user_id: "mine" };
  const entries = Array.from({ length: 10 }, (_, index) => ({
    ...reader,
    position: index + 1,
    username: `reader-${index}`,
  }));
  const mine = { ...reader, position: 11, username: "mine" };
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
    await screen.findAllByRole("link", { name: rankingName("Reader", 1, 21, true) }),
  ).toHaveLength(2);
  for (const title of ["Longest streak", "Most reading days"]) {
    const board = screen.getByRole("region", { name: title });
    expect(within(board).getAllByRole("listitem")).toHaveLength(10);
    expect(
      within(within(board).getByLabelText(`Your ${title.toLowerCase()} ranking`)).getByRole(
        "link",
        { name: rankingName("Reader", 1, 21, true) },
      ),
    ).toBeTruthy();
  }
});

it.each(["mine", ""])(
  "does not label another reader as You when ranking responses disagree for username %j",
  async (username) => {
    state.user = { user_id: "mine" };
    const other = { ...reader, username: username ? "other" : "", display_name: "Other reader" };
    const mine = { ...reader, username, display_name: "My public name" };
    network.mockImplementation((url: string) =>
      Promise.resolve(
        Response.json(
          url.endsWith("/me")
            ? { longest_streak: mine, reading_days: mine }
            : { longest_streak: [other], reading_days: [other] },
        ),
      ),
    );
    render(<Leaderboard />);
    expect(await screen.findAllByText("You")).toHaveLength(2);
    for (const title of ["Longest streak", "Most reading days"]) {
      const board = screen.getByRole("region", { name: title });
      expect(within(within(board).getByRole("listitem")).queryByText("You")).toBeNull();
      const personal = within(board).getByLabelText(`Your ${title.toLowerCase()} ranking`);
      expect(within(personal).getByText("My public name")).toBeTruthy();
      expect(within(personal).getByText("You")).toBeTruthy();
    }
  },
);

it("keeps the public rankings when personal ranks fail and lets the reader retry", async () => {
  state.user = { user_id: "mine" };
  network.mockImplementation((url: string) =>
    Promise.resolve(
      url.endsWith("/me") ? new Response(null, { status: 503 }) : Response.json(publicBoards),
    ),
  );
  render(<Leaderboard />);
  expect(await screen.findAllByRole("button", { name: "Retry your ranking" })).toHaveLength(2);
  expect(screen.getByRole("link", { name: rankingName("Reader", 1, 21, false) })).toBeTruthy();
  network.mockImplementation((url: string) =>
    Promise.resolve(Response.json(url.endsWith("/me") ? ownRanks : publicBoards)),
  );
  fireEvent.click(screen.getAllByRole("button", { name: "Retry your ranking" })[0]);
  expect(
    await screen.findByRole("link", { name: rankingName("Reader", 15, 4, true) }),
  ).toBeTruthy();
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
  { username: null, display_name: null, published: false, action: "Make your profile public" },
  { username: null, display_name: null, published: true, action: "Set your display name" },
  { username: null, display_name: " \t\n ", published: true, action: "Set your display name" },
  { username: null, display_name: "Named reader", published: true, action: "Start reading" },
  { username: "mine", display_name: null, published: false, action: "Make your profile public" },
  { username: "mine", display_name: null, published: true, action: "Start reading" },
])(
  "offers $action for an unranked reader",
  async ({ username, display_name, published, action }) => {
    state.user = { user_id: "mine" };
    state.profile = {
      display_name,
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
    const actions = await screen.findAllByRole("link", { name: action });
    expect(actions).toHaveLength(2);
    for (const link of actions) {
      expect(link.getAttribute("href")).toBe(
        action === "Start reading" ? "/latest" : "/settings/profile",
      );
    }
  },
);

it("does not show a previous account's rank while the next account loads", async () => {
  state.user = { user_id: "mine" };
  const view = render(<Leaderboard />);
  await screen.findByRole("link", { name: rankingName("Reader", 15, 4, true) });
  let finish!: (response: Response) => void;
  network.mockImplementation(
    () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  );
  state.user = { user_id: "other" };
  view.rerender(<Leaderboard />);
  expect(screen.queryByRole("link", { name: rankingName("Reader", 15, 4, true) })).toBeNull();
  await act(async () => finish(Response.json({ longest_streak: null, reading_days: null })));
});

function rankingName(name: string, rank: number, days: number, own: boolean) {
  return new RegExp(
    `^#${rank.toLocaleString("en-US")}\\s*${name}${own ? "\\s*You" : ""}\\s*@.*${days}\\s*days$`,
  );
}

it("keeps named unclaimed readers unlinked and highlights only the signed-in reader", async () => {
  state.user = { user_id: "mine" };
  const entries = [
    { ...reader, position: 1, username: "", display_name: "  Unclaimed reader  " },
    { ...reader, position: 2, username: "", display_name: "Another reader" },
  ];
  network.mockImplementation((url: string) =>
    Promise.resolve(
      Response.json(
        url.endsWith("/me")
          ? { longest_streak: entries[1], reading_days: entries[1] }
          : { longest_streak: entries, reading_days: entries },
      ),
    ),
  );
  render(<Leaderboard />);
  await screen.findAllByText("You");
  for (const title of ["Longest streak", "Most reading days"]) {
    const board = screen.getByRole("region", { name: title });
    const rows = within(board).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(within(board).queryByRole("link")).toBeNull();
    expect(rows[0].textContent).toContain("Unclaimed reader");
    expect(rows[0].textContent).not.toContain("You");
    expect(rows[0].textContent).not.toContain("  Unclaimed reader  ");
    expect(rows[1].textContent).toContain("Another readerYou");
    expect(within(board).queryByLabelText(`Your ${title.toLowerCase()} ranking`)).toBeNull();
  }
});

it.each([undefined, null, "", " \t\n "])(
  "omits anonymous public and personal entries with display name %j",
  async (display_name) => {
    state.user = { user_id: "mine" };
    state.profile = { username: null, display_name: null, avatar_url: null };
    const named = { ...reader, username: "real-handle", display_name: " \t " };
    const anonymous = { ...reader, position: 2, username: "", display_name };
    network.mockImplementation((url: string) =>
      Promise.resolve(
        Response.json(
          url.endsWith("/me")
            ? { longest_streak: anonymous, reading_days: anonymous }
            : { longest_streak: [named, anonymous], reading_days: [named, anonymous] },
        ),
      ),
    );
    render(<Leaderboard />);
    expect(await screen.findAllByRole("link", { name: "Set your display name" })).toHaveLength(2);
    for (const title of ["Longest streak", "Most reading days"]) {
      const board = screen.getByRole("region", { name: title });
      expect(within(board).getAllByRole("listitem")).toHaveLength(1);
      expect(
        within(board).getByRole("link", { name: rankingName("real-handle", 1, 21, false) }),
      ).toBeTruthy();
      expect(within(board).queryByText("You")).toBeNull();
      expect(within(board).queryByText("Reader", { exact: true })).toBeNull();
    }
  },
);

it("shows empty boards when a legacy response contains only anonymous entries", async () => {
  const anonymous = { ...reader, username: "", display_name: null };
  network.mockResolvedValue(
    Response.json({ longest_streak: [anonymous], reading_days: [anonymous] }),
  );
  render(<Leaderboard />);
  expect(await screen.findAllByText("No rankings yet.")).toHaveLength(2);
  expect(screen.queryByRole("listitem")).toBeNull();
  expect(screen.queryByText("Reader", { exact: true })).toBeNull();
});
