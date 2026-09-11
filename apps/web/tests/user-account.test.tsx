// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  UserProvider,
  UserAccount,
  PersonalFeedNav,
} from "@/components/user-account";
import { TopicPreferences } from "@/components/topic-preferences";
import type { Topic } from "@/lib/types";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("does not redirect anonymous users and starts authentication only on demand", async () => {
  const fetcher = vi.fn((url: string) =>
    Promise.resolve(
      url.endsWith("/me")
        ? Response.json(null)
        : Response.json({ enabled: true }),
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  render(
    <UserProvider>
      <UserAccount />
      <PersonalFeedNav />
      <PersonalFeedNav mobile />
      <p>Public articles</p>
    </UserProvider>,
  );
  expect(screen.getByText("Public articles")).toBeTruthy();
  expect(screen.queryAllByRole("link", { name: "My feed" })).toHaveLength(0);
  expect(screen.getByRole("link", { name: "Sign in" })).toHaveProperty(
    "href",
    "http://localhost:3000/api/v1/user/auth/login",
  );
  expect(screen.queryAllByRole("link", { name: "My feed" })).toHaveLength(0);
  expect(fetcher.mock.calls.every(([url]) => !url.includes("auth/login"))).toBe(
    true,
  );
});
it("loads saved preferences and saves topic selections with CSRF", async () => {
  const topic: Topic = {
    id: "topic-a",
    name: "Python",
    slug: "python",
    kind: "technology",
    logo_url: null,
    description: null,
    ai_description: null,
    website_url: null,
  };
  const fetcher = vi.fn((url: string, init?: RequestInit) =>
    Promise.resolve(
      Response.json(
        url.endsWith("/me")
          ? {
              user_id: "user-a",
              csrf_token: "csrf",
              name: "User",
              expires_at: 4102444800,
            }
          : { topic_ids: init?.method === "PUT" ? [topic.id] : [] },
      ),
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  render(
    <UserProvider>
      <TopicPreferences topics={[topic]} />
    </UserProvider>,
  );
  const checkbox = await screen.findByRole("checkbox", { name: "Python" });
  fireEvent.click(checkbox);
  fireEvent.click(screen.getByRole("button", { name: "Save topics" }));
  await screen.findByText("Your topics are saved.");
  const write = fetcher.mock.calls.find(
    ([, init]) => init?.method === "PUT",
  )![1]!;
  expect(write.body).toBe('{"topic_ids":["topic-a"]}');
  expect(write.headers).toEqual({
    "Content-Type": "application/json",
    "X-CSRF-Token": "csrf",
  });
  expect(write.cache).toBe("no-store");
});
it("removes private controls when the server reports an expired session", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json({ user_id: "user", csrf_token: "csrf" }),
      ),
  );
  render(
    <UserProvider>
      <UserAccount />
    </UserProvider>,
  );
  await screen.findByRole("button", { name: "User menu: Your account" });
  window.dispatchEvent(new Event("devfeed:user-session-expired"));
  await waitFor(() =>
    expect(screen.getByRole("link", { name: "Sign in" })).toBeTruthy(),
  );
});

it("shows My feed in both navigation layouts only while signed in", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json({ user_id: "user", csrf_token: "csrf" }),
      ),
  );
  render(
    <UserProvider>
      <PersonalFeedNav />
      <PersonalFeedNav mobile />
    </UserProvider>,
  );
  expect(screen.queryAllByRole("link", { name: "My feed" })).toHaveLength(0);
  await waitFor(() =>
    expect(screen.getAllByRole("link", { name: "My feed" })).toHaveLength(2),
  );
  window.dispatchEvent(new Event("devfeed:user-session-expired"));
  await waitFor(() =>
    expect(screen.queryAllByRole("link", { name: "My feed" })).toHaveLength(0),
  );
});
