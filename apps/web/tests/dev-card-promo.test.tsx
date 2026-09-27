// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { DevCardPromo } from "@/components/dev-card-promo";
import { readDevCardDraft, saveDevCardDraft } from "@/lib/dev-card-draft";
import * as runtime from "@/lib/reader-runtime";
import type { DevCardData } from "@/lib/dev-card";
const featured = {
  username: "asaharan",
  display_name: "Abhimanyu Saharan",
  avatar_url: "https://example.com/abhimanyu.png",
  bio: "Building DevFeed",
  location: "India",
  stack: [
    {
      topic_id: "k8s",
      name: "Kubernetes",
      kind: "platform",
      section: "primary",
      logo_url: "https://example.com/k8s.svg",
    },
  ],
  reading_streak: { current_days: 2, longest_days: 9, total_days: 17 },
};
const state = vi.hoisted(() => ({
  user: null as { user_id: string } | null,
  loading: false,
  unavailable: false,
}));
vi.mock("@/components/user-account", () => ({ useUser: () => state }));
vi.mock("@/components/dev-card-artwork", () => ({
  DevCardArtwork: ({ data }: { data: DevCardData }) => (
    <div>
      {data.name}
      <pre data-testid="card-data">{JSON.stringify(data)}</pre>
    </div>
  ),
}));
beforeEach(() => {
  vi.spyOn(runtime, "readerRequest").mockResolvedValue(
    new Response(JSON.stringify({ profile: featured })),
  );
  vi.spyOn(performance, "now").mockReturnValue(30_000);
  window.matchMedia = vi.fn().mockReturnValue({ matches: false });
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
  sessionStorage.clear();
  state.user = null;
  state.loading = false;
  state.unavailable = false;
});

it("shows asaharan's current public card without fabricated sample metadata", async () => {
  render(<DevCardPromo requested />);
  await screen.findByText("Abhimanyu Saharan");
  expect(runtime.readerRequest).toHaveBeenCalledWith(
    "/api/v1/users/asaharan?include_activity=false",
    expect.objectContaining({
      credentials: "omit",
      cache: "no-store",
      signal: expect.any(AbortSignal),
    }),
  );
  const card = JSON.parse(screen.getByTestId("card-data").textContent!);
  expect(card).toMatchObject({
    username: "asaharan",
    avatar: featured.avatar_url,
    bio: featured.bio,
    location: "India",
  });
  expect(card.technologies).toEqual([
    { id: "k8s", name: "Kubernetes", kind: "platform", logoUrl: "https://example.com/k8s.svg" },
  ]);
  expect(card.stats.map((item: { value: number }) => item.value)).toEqual([2, 9, 17]);
  expect(screen.queryByText("Alex Morgan")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Create your dev card" }));
  fireEvent.change(screen.getByLabelText("Your display name"), { target: { value: "Maya" } });
  const personal = JSON.parse(screen.getByTestId("card-data").textContent!);
  expect(personal).toMatchObject({
    name: "Maya",
    avatar: null,
    username: null,
    technologies: [],
    stats: [],
  });
});

it("keeps creation available when the featured profile is private or unavailable", async () => {
  vi.mocked(runtime.readerRequest).mockResolvedValue(new Response(null, { status: 404 }));
  render(<DevCardPromo requested />);
  await screen.findByText("The public card is temporarily unavailable.");
  expect(screen.queryByTestId("card-data")).toBeNull();
  fireEvent.click(await screen.findByRole("button", { name: "Create your dev card" }));
  expect(screen.getByLabelText("Your display name")).toBeTruthy();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});
it("waits for authentication to resolve", () => {
  state.loading = true;
  render(<DevCardPromo />);
  expect(screen.queryByRole("region")).toBeNull();
});
it("hides from signed-in users without a draft", () => {
  state.user = { user_id: "one" };
  render(<DevCardPromo />);
  expect(screen.queryByRole("region")).toBeNull();
});
it("lets returning extension users finish their saved preview", async () => {
  state.user = { user_id: "one" };
  saveDevCardDraft({ name: "Maya", stack: [], ready: true, created: Date.now() });
  render(<DevCardPromo />);
  expect(
    (
      await screen.findByRole("link", { name: /Finish your dev card/ }, { timeout: 3000 })
    ).getAttribute("href"),
  ).toBe("/settings/profile");
});
it("ignores outside clicks and dismisses with the close button for the session", async () => {
  const view = render(<DevCardPromo />);
  const dialog = await screen.findByRole(
    "dialog",
    { name: "Your dev card preview" },
    { timeout: 3000 },
  );
  fireEvent.click(dialog, { clientX: -10, clientY: -10 });
  expect(screen.getByRole("dialog", { name: "Your dev card preview" })).toBeTruthy();
  fireEvent.click(
    await screen.findByRole("button", { name: "Dismiss dev card preview" }, { timeout: 3000 }),
  );
  view.unmount();
  render(<DevCardPromo />);
  expect(screen.queryByRole("region")).toBeNull();
});
it("ignores expired or malformed drafts", () => {
  saveDevCardDraft({ name: "Maya", stack: [], ready: true, created: Date.now() - 86400001 });
  expect(readDevCardDraft()).toBeNull();
  sessionStorage.setItem(
    "devfeed:dev-card-draft",
    JSON.stringify({ name: "Maya", stack: [null], ready: true, created: Date.now() }),
  );
  expect(readDevCardDraft()).toBeNull();
});

it("allows previewing during an auth outage without a broken signup link", async () => {
  state.unavailable = true;
  render(<DevCardPromo />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Create your dev card" }, { timeout: 3000 }),
  );
  fireEvent.change(screen.getByLabelText("Your display name"), { target: { value: "Maya" } });
  expect(screen.getByText("Maya")).toBeTruthy();
  expect(
    (screen.getByRole("button", { name: "Save my dev card" }) as HTMLButtonElement).disabled,
  ).toBe(true);
  expect(screen.queryByRole("link", { name: "Save my dev card" })).toBeNull();
});

it("waits until 30 seconds after page entry and then waits for other dialogs", async () => {
  vi.useFakeTimers();
  vi.spyOn(performance, "now").mockReturnValue(10_000);
  const welcome = document.createElement("dialog");
  document.body.append(welcome);
  welcome.showModal();
  try {
    render(<DevCardPromo />);
    await act(() => vi.advanceTimersByTimeAsync(20));
    await act(() => vi.advanceTimersByTimeAsync(19_999));
    expect(screen.queryByRole("dialog", { name: "Your dev card preview" })).toBeNull();
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(screen.queryByRole("dialog", { name: "Your dev card preview" })).toBeNull();
    welcome.close();
    await act(() => vi.advanceTimersByTimeAsync(250));
    expect(screen.getByRole("dialog", { name: "Your dev card preview" })).toBeTruthy();
  } finally {
    welcome.remove();
  }
});

it("does not reveal before the visitor has spent 30 seconds on the page", async () => {
  vi.useFakeTimers();
  vi.spyOn(performance, "now").mockReturnValue(0);
  render(<DevCardPromo />);
  await act(() => vi.advanceTimersByTimeAsync(20));
  await act(() => vi.advanceTimersByTimeAsync(29_999));
  expect(screen.queryByRole("dialog", { name: "Your dev card preview" })).toBeNull();
  await act(() => vi.advanceTimersByTimeAsync(1));
  expect(screen.getByRole("dialog", { name: "Your dev card preview" })).toBeTruthy();
});

it("lets visitors explicitly create a card without waiting or clearing dismissal", async () => {
  vi.spyOn(performance, "now").mockReturnValue(0);
  sessionStorage.setItem("devfeed:dev-card-promo-dismissed", "true");
  render(<DevCardPromo requested />);
  expect(await screen.findByRole("dialog", { name: "Your dev card preview" })).toBeTruthy();
});
