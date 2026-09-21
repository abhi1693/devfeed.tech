// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PersonalFeed } from "@/components/personal-feed";
import { UserProvider } from "@/components/user-account";
import { article, topic } from "./fixtures";

vi.mock("@/components/infinite-feed", () => ({
  InfiniteFeed: () => <p>Your recommended articles</p>,
}));
beforeEach(() => {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
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
      this.dispatchEvent(new Event("close"));
    },
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function setup({
  existing = [] as string[],
  failLoad = false,
  failPreferences = false,
  failSave = false,
  interests = false,
  cursor = "",
  signedIn = true,
  count = 12,
  laterPage = undefined as ((signal?: AbortSignal | null) => Promise<Response>) | undefined,
} = {}) {
  let saved = false;
  const fetcher = vi.fn(async (input: string, init?: RequestInit) => {
    const url = new URL(input, "http://localhost");
    const path = url.pathname;
    if (path.endsWith("auth/me"))
      return Response.json(signedIn ? { user_id: "reader", csrf_token: "csrf" } : null);
    if (path.endsWith("auth/config")) return Response.json({ enabled: true });
    if (path.endsWith("settings/profile"))
      return Response.json({ display_name: null, avatar_url: null });
    if (path.endsWith("/user/feed"))
      return Response.json({
        status: "ready",
        has_interests: saved || interests,
        items: saved ? [article] : [],
        next_cursor: null,
        reasons: {},
      });
    if (path.endsWith("/preferences")) {
      if (init?.method === "PUT") {
        if (failSave) {
          failSave = false;
          return Response.json({}, { status: 503 });
        }
        saved = true;
        existing = JSON.parse(String(init.body)).topic_ids;
        return Response.json({ topic_ids: existing });
      }
      if (failPreferences) {
        failPreferences = false;
        return Response.json({}, { status: 503 });
      }
      return Response.json({ topic_ids: existing });
    }
    if (path === "/api/v1/topics") {
      expect(url.searchParams.get("sort")).toBe("articles");
      if (failLoad) {
        failLoad = false;
        return Response.json({}, { status: 503 });
      }
      if (url.searchParams.get("q"))
        return Response.json({
          items:
            url.searchParams.get("q") === "python"
              ? [{ ...topic, id: "python", name: "Python" }]
              : [],
          next_cursor: null,
        });
      if (url.searchParams.get("offset") === "60" && laterPage) return laterPage(init?.signal);
      return Response.json(
        url.searchParams.get("offset") === "60"
          ? { items: [{ ...topic, id: "python", name: "Python" }], next_cursor: null }
          : {
              items: Array.from({ length: count }, (_, index) => ({
                ...topic,
                id: `topic-${index}`,
                name: `Topic ${index}`,
              })),
              next_cursor: count < 3 ? null : "60",
            },
      );
    }
    throw new Error(`Unexpected request: ${input}`);
  });
  vi.stubGlobal("fetch", fetcher);
  const view = render(
    <UserProvider>
      <PersonalFeed cursor={cursor || undefined} />
    </UserProvider>,
  );
  return {
    ...view,
    fetcher,
    user: userEvent.setup(),
    writes: () => fetcher.mock.calls.filter(([, init]) => init?.method === "PUT"),
  };
}

it("shows the modal over My feed, retains ranked order across pages, and saves 3 topics with CSRF", async () => {
  const { user, writes, fetcher } = setup();
  await screen.findByRole("checkbox", { name: "Topic 0" });
  expect(screen.getByRole("dialog", { name: "Choose your topics" }).hasAttribute("open")).toBe(
    true,
  );
  expect(screen.getByRole("region", { name: "Feed controls" })).toBeTruthy();
  await screen.findByRole("checkbox", { name: "Python" });
  expect(screen.getAllByRole("checkbox").map((input) => input.parentElement?.textContent)).toEqual([
    ...Array.from({ length: 12 }, (_, index) => `Topic ${index}`),
    "Python",
  ]);
  const save = screen.getByRole("button", { name: "Save" });
  expect(save).toHaveProperty("disabled", true);
  await user.click(screen.getByRole("checkbox", { name: "Topic 0" }));
  await user.click(screen.getByRole("checkbox", { name: "Topic 1" }));
  expect(save).toHaveProperty("disabled", true);
  fireEvent.submit(save.closest("form")!);
  expect(writes()).toHaveLength(0);
  await user.type(screen.getByRole("searchbox"), "python");
  await user.click(await screen.findByRole("checkbox", { name: "Python" }));
  await user.clear(screen.getByRole("searchbox"));
  expect(await screen.findByRole("checkbox", { name: "Topic 0" })).toHaveProperty("checked", true);
  await user.click(save);
  await screen.findByText("Your recommended articles");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.body.style.overflow).toBe("");
  expect(writes()).toHaveLength(1);
  expect(writes()[0]).toEqual([
    "/api/v1/user/preferences",
    expect.objectContaining({
      headers: { "Content-Type": "application/json", "X-CSRF-Token": "csrf" },
      body: JSON.stringify({ topic_ids: ["topic-0", "topic-1", "python"] }),
    }),
  ]);
  expect(
    fetcher.mock.calls.some(([url]) => url.includes("sources") || url === "/api/v1/feed"),
  ).toBe(false);
});

it("allows more than 3 topics and retains selections after a failed save", async () => {
  const { user, writes } = setup({ failSave: true });
  await screen.findByRole("checkbox", { name: "Topic 0" });
  for (const index of [0, 1, 2, 3])
    await user.click(screen.getByRole("checkbox", { name: `Topic ${index}` }));
  await user.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Couldn’t save your topics. Please try again.");
  expect(screen.getAllByRole("checkbox", { checked: true })).toHaveLength(4);
  await user.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Your recommended articles");
  expect(writes()).toHaveLength(2);
  expect(JSON.parse(String(writes()[1][1]?.body)).topic_ids).toHaveLength(4);
});

it("retries a failed catalog and shows an empty search result", async () => {
  const { user } = setup({ failLoad: true });
  await screen.findByText("Couldn’t load topics.");
  await user.click(await screen.findByRole("button", { name: "Try again" }));
  await screen.findByRole("checkbox", { name: "Python" });
  await user.type(screen.getByRole("searchbox"), "no match");
  expect(await screen.findByText("No topics match your search.")).toBeTruthy();
});

it("does not treat a preferences outage as an empty selection", async () => {
  const { user, fetcher } = setup({ failPreferences: true, existing: ["existing"] });
  await screen.findByText("Couldn’t load your topics.");
  expect(screen.queryByRole("dialog")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  expect(fetcher.mock.calls.some(([url]) => url.startsWith("/api/v1/topics"))).toBe(false);
});

it("leaves the feed usable when the modal is closed without saving", async () => {
  const { user, writes } = setup();
  await screen.findByRole("checkbox", { name: "Topic 0" });
  expect(document.body.style.overflow).toBe("hidden");
  await user.click(screen.getByRole("button", { name: "Close" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByRole("region", { name: "Feed controls" })).toBeTruthy();
  expect(document.body.style.overflow).toBe("");
  expect(writes()).toHaveLength(0);
});

it.each([{ existing: ["one-topic"] }, { cursor: "next" }, { signedIn: false }])(
  "does not prompt existing topic followers, cursor pages, or guests: %j",
  async (options) => {
    const { fetcher } = setup(options);
    await waitFor(() => expect(fetcher).toHaveBeenCalled());
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(fetcher.mock.calls.some(([url]) => url.startsWith("/api/v1/topics"))).toBe(false);
  },
);

it("prompts a reader with no topics even when other interests exist", async () => {
  setup({ interests: true });
  expect(await screen.findByRole("checkbox", { name: "Topic 0" })).toBeTruthy();
});

it("explains when the available catalog cannot meet the minimum", async () => {
  setup({ count: 2 });
  await screen.findByText("There aren’t enough topics available yet. Try again later.");
  expect(await screen.findByRole("button", { name: "Save" })).toHaveProperty("disabled", true);
});

it("shows and saves the first page while a later page is pending, then aborts loading", async () => {
  let laterSignal: AbortSignal | null | undefined;
  const { user, fetcher, writes } = setup({
    laterPage: (signal) => {
      laterSignal = signal;
      return new Promise((_resolve, reject) =>
        signal?.addEventListener("abort", () => reject(signal.reason), { once: true }),
      );
    },
  });
  await screen.findByRole("checkbox", { name: "Topic 0" });
  await screen.findByText("Loading more topics…");
  expect(laterSignal).toBeDefined();
  const firstSignal = fetcher.mock.calls.find(([url]) => url.includes("offset=0"))?.[1]?.signal;
  expect(laterSignal).not.toBe(firstSignal);
  for (const index of [0, 1, 2])
    await user.click(screen.getByRole("checkbox", { name: `Topic ${index}` }));
  await user.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Your recommended articles");
  expect(writes()).toHaveLength(1);
  expect(laterSignal?.aborted).toBe(true);
});

it("keeps loaded topics and selections when a later page fails and resumes at that page", async () => {
  let attempts = 0;
  const { user, fetcher } = setup({
    laterPage: async () =>
      ++attempts === 1
        ? Response.json({}, { status: 503 })
        : Response.json({ items: [{ ...topic, id: "python", name: "Python" }], next_cursor: null }),
  });
  await screen.findByText("Couldn’t load more topics.");
  await user.click(screen.getByRole("checkbox", { name: "Topic 0" }));
  await user.click(screen.getByRole("button", { name: "Try again" }));
  await screen.findByRole("checkbox", { name: "Python" });
  expect(await screen.findByRole("checkbox", { name: "Topic 0" })).toHaveProperty("checked", true);
  expect(fetcher.mock.calls.filter(([url]) => url.includes("offset=0"))).toHaveLength(1);
  expect(fetcher.mock.calls.filter(([url]) => url.includes("offset=60"))).toHaveLength(2);
  expect(screen.queryByRole("alert")).toBeNull();
});

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
