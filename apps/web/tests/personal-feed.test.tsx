// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { PersonalFeed } from "@/components/personal-feed";
import type { ReactNode } from "react";

vi.mock("@/components/feed-onboarding", () => ({ FeedOnboarding: () => null }));
vi.mock("@/components/user-account", () => ({
  AccountGate: ({ children }: { children: ReactNode }) => children,
}));
vi.mock("@/components/article-grid", () => ({
  ArticleGrid: ({ articles }: { articles: { title: string }[] }) => (
    <div>
      {articles.map((article) => (
        <p key={article.title}>{article.title}</p>
      ))}
    </div>
  ),
}));
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
const ready = {
  status: "ready",
  has_interests: true,
  items: [{ title: "Recommended article" }],
  next_cursor: null,
  reasons: {},
};

it("offers the new generation without hiding a previously loaded cursor page", async () => {
  vi.useFakeTimers();
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ ...ready, status: "refreshing" }))
    .mockResolvedValue(Response.json({ detail: "Generation changed" }, { status: 409 }));
  vi.stubGlobal("fetch", fetcher);
  await act(async () => {
    render(<PersonalFeed cursor="old-cursor" />);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(screen.getByText("Recommended article")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Show updated feed" }).getAttribute("href")).toBe("/");
  expect(screen.queryByText("Updating recommendations in the background…")).toBeNull();
});

it("loads and finishes preparing recommendations without window focus, even while hidden", async () => {
  vi.useFakeTimers();
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  vi.spyOn(document, "hasFocus").mockReturnValue(false);
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ ...ready, status: "refreshing", items: [] }))
    .mockResolvedValue(Response.json(ready));
  vi.stubGlobal("fetch", fetcher);
  await act(async () => {
    render(<PersonalFeed />);
  });
  expect(fetcher).toHaveBeenCalledTimes(1);
  await act(async () => {
    window.dispatchEvent(new Event("blur"));
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(screen.getByText("Recommended article")).toBeTruthy();
  await act(async () => {
    window.dispatchEvent(new Event("focus"));
    await vi.advanceTimersByTimeAsync(60000);
  });
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("keeps an in-flight load on blur and cancels it on unmount", async () => {
  let resolve!: (response: Response) => void;
  const fetcher = vi.fn<typeof fetch>(
    () =>
      new Promise<Response>((done) => {
        resolve = done;
      }),
  );
  vi.stubGlobal("fetch", fetcher);
  const view = render(<PersonalFeed />);
  const signal = fetcher.mock.calls[0][1]!.signal as AbortSignal;
  await act(async () => {
    window.dispatchEvent(new Event("blur"));
    resolve(Response.json(ready));
  });
  expect(signal.aborted).toBe(false);
  expect(screen.getByText("Recommended article")).toBeTruthy();
  view.unmount();
  expect(signal.aborted).toBe(true);
});

it("polls pending recommendations and replaces them when preparation completes", async () => {
  vi.useFakeTimers();
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ ...ready, status: "refreshing", items: [] }))
    .mockResolvedValue(Response.json(ready));
  vi.stubGlobal("fetch", fetcher);
  await act(async () => {
    render(<PersonalFeed />);
  });
  expect(screen.getByText("Updating your feed")).toBeTruthy();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(screen.getByText("Recommended article")).toBeTruthy();
  expect(fetcher).toHaveBeenCalledTimes(2);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60000);
  });
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("drops displayed recommendations when interests change and cancels pending polling on unmount", async () => {
  vi.useFakeTimers();
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json(ready))
    .mockResolvedValue(Response.json({ ...ready, status: "refreshing", items: [] }));
  vi.stubGlobal("fetch", fetcher);
  let view: ReturnType<typeof render>;
  await act(async () => {
    view = render(<PersonalFeed />);
  });
  expect(screen.getByText("Recommended article")).toBeTruthy();
  await act(async () => {
    window.dispatchEvent(new Event("devfeed:interests-changed"));
  });
  expect(screen.queryByText("Recommended article")).toBeNull();
  expect(screen.getByText("Updating your feed")).toBeTruthy();
  view!.unmount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60000);
  });
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("offers a first-page restart when a cursor belongs to an old generation", async () => {
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({}, { status: 409 })));
  render(<PersonalFeed cursor="old-generation" />);
  expect(await screen.findByText("Your feed has been updated")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Show updated feed" }).getAttribute("href")).toBe("/");
});

it("keeps the previous generation visible during refresh and replaces it when ready", async () => {
  vi.useFakeTimers();
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ ...ready, generation: "old" }))
    .mockResolvedValueOnce(Response.json({ ...ready, generation: "old", status: "refreshing" }))
    .mockResolvedValueOnce(
      Response.json({ ...ready, generation: "new", items: [{ title: "New recommendation" }] }),
    );
  vi.stubGlobal("fetch", fetcher);
  await act(async () => {
    render(<PersonalFeed />);
  });
  await act(async () => {
    window.dispatchEvent(new Event("devfeed:interests-changed"));
  });
  expect(screen.getByText("Recommended article")).toBeTruthy();
  expect(screen.queryByText("Updating your feed")).toBeNull();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(screen.getByText("New recommendation")).toBeTruthy();
  expect(screen.queryByText("Recommended article")).toBeNull();
});
