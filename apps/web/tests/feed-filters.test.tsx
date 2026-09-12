// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { FeedFiltersBar } from "@/components/feed-filters";
import { FeedPreferencesProvider } from "@/components/feed-preferences";
import type { UserIdentity } from "@/lib/user";
import userEvent from "@testing-library/user-event";
import { parseFilters } from "@/lib/feed-query";
import { source } from "./fixtures";

const router = vi.hoisted(() => ({ push: vi.fn() }));
const session = vi.hoisted(() => ({ user: null as UserIdentity | null, loading: false }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/components/user-account", () => ({ useUser: () => session }));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  session.user = null;
});

it("applies filters to a content route without losing the topic or search", async () => {
  render(
    <FeedFiltersBar
      filters={parseFilters({
        topic: "python",
        content_type: "tutorial",
        q: "guide",
        cursor: "old",
      })}
      sources={[source]}
    />,
  );
  const user = userEvent.setup();
  fireEvent.click(screen.getByText("Filters"));
  await user.click(screen.getByRole("combobox", { name: "Language" }));
  await user.click(screen.getByRole("option", { name: "English" }));
  await user.click(screen.getByRole("combobox", { name: "Source" }));
  await user.click(screen.getByRole("option", { name: source.name }));
  fireEvent.submit(screen.getByLabelText("Language").closest("form")!);
  const url = new URL(router.push.mock.calls[0][0], "http://localhost");
  expect(url.pathname).toBe("/topics/python/tutorials");
  expect(Object.fromEntries(url.searchParams)).toEqual({
    q: "guide",
    language: "en",
    source_id: source.id,
  });
});

it("removes the source from the path when selecting all sources", async () => {
  render(
    <FeedFiltersBar
      filters={parseFilters({ source_id: source.id, content_type: "news" })}
      sources={[source]}
    />,
  );
  fireEvent.change(document.querySelector('select[name="source_id"]')!, { target: { value: "" } });
  fireEvent.submit(screen.getByLabelText("Source").closest("form")!);
  expect(router.push).toHaveBeenCalledWith("/news");
});

it("shows only saved content types that have data on the current topic", async () => {
  session.user = {
    user_id: "reader",
    csrf_token: "csrf",
    name: "Reader",
    email: null,
    expires_at: 9999999999,
  };
  const fetcher = vi
    .fn()
    .mockResolvedValue(Response.json({ view: "cards", content_types: ["tutorial", "news"] }));
  vi.stubGlobal("fetch", fetcher);
  render(
    <FeedPreferencesProvider>
      <FeedFiltersBar
        filters={parseFilters({ topic: "python" })}
        topicPage
        sources={[]}
        availableTypes={["article", "tutorial", "opinion"]}
      />
    </FeedPreferencesProvider>,
  );
  const tabs = within(screen.getByRole("navigation", { name: "Article type" }));
  expect(tabs.queryByRole("link", { name: "Articles" })).toBeNull();
  expect((await tabs.findByRole("link", { name: "Tutorials" })).getAttribute("href")).toBe(
    "/topics/python/tutorials",
  );
  expect(tabs.getAllByRole("link").map((link) => link.textContent)).toEqual(["All", "Tutorials"]);
  expect(tabs.getByRole("link", { name: "All" }).getAttribute("href")).toBe("/topics/python");
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("keeps all available content types for guests without fetching preferences", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  render(
    <FeedPreferencesProvider>
      <FeedFiltersBar
        filters={parseFilters({})}
        sources={[]}
        availableTypes={["article", "opinion"]}
      />
    </FeedPreferencesProvider>,
  );
  const tabs = within(screen.getByRole("navigation", { name: "Article type" }));
  await tabs.findByRole("link", { name: "Articles" });
  expect(tabs.getAllByRole("link").map((link) => link.textContent)).toEqual([
    "All",
    "Articles",
    "Opinions",
  ]);
  expect(fetcher).not.toHaveBeenCalled();
});
