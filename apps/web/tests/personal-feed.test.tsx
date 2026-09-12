// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  render,
  screen,
} from "@testing-library/react";
import { PersonalFeed } from "@/components/personal-feed";
import type { ReactNode } from "react";

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

it("waits while hidden, pauses pending recommendations on blur, and resumes immediately", async () => {
  vi.useFakeTimers();
  const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ ...ready, status: "refreshing", items: [] })).mockResolvedValue(Response.json(ready));
  vi.stubGlobal("fetch", fetcher);
  await act(async () => { render(<PersonalFeed />); await vi.advanceTimersByTimeAsync(60000); });
  expect(fetcher).not.toHaveBeenCalled();
  visibility.mockReturnValue("visible");
  await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
  expect(fetcher).toHaveBeenCalledTimes(1);
  await act(async () => { window.dispatchEvent(new Event("blur")); await vi.advanceTimersByTimeAsync(60000); });
  expect(fetcher).toHaveBeenCalledTimes(1);
  await act(async () => { window.dispatchEvent(new Event("focus")); });
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(screen.getByText("Recommended article")).toBeTruthy();
  await act(async () => { window.dispatchEvent(new Event("blur")); window.dispatchEvent(new Event("focus")); await vi.advanceTimersByTimeAsync(60000); });
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("polls pending recommendations and replaces them when preparation completes", async () => {
  vi.useFakeTimers();
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(
      Response.json({ ...ready, status: "refreshing", items: [] }),
    )
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
    .mockResolvedValue(
      Response.json({ ...ready, status: "refreshing", items: [] }),
    );
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
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json({}, { status: 409 })),
  );
  render(<PersonalFeed cursor="old-generation" />);
  expect(await screen.findByText("Your feed has been updated")).toBeTruthy();
  expect(
    screen
      .getByRole("link", { name: "Show updated feed" })
      .getAttribute("href"),
  ).toBe("/my-feed");
});
