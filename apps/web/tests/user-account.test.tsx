// @vitest-environment jsdom
import { useEffect } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  UserProvider,
  UserAccount,
  PersonalFeedNav,
  ReadLaterNav,
} from "@/components/user-account";
import { TopicFollowsProvider } from "@/components/topic-follows";
import { TopicPreferences } from "@/components/topic-preferences";
import type { Topic } from "@/lib/types";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("does not redirect anonymous users and starts authentication only on demand", async () => {
  const fetcher = vi.fn((url: string) =>
    Promise.resolve(url.endsWith("/me") ? Response.json(null) : Response.json({ enabled: true })),
  );
  vi.stubGlobal("fetch", fetcher);
  render(
    <UserProvider>
      <UserAccount />
      <PersonalFeedNav />
      <PersonalFeedNav mobile />
      <ReadLaterNav />
      <ReadLaterNav mobile />
      <p>Public articles</p>
    </UserProvider>,
  );
  expect(screen.getByText("Public articles")).toBeTruthy();
  expect(screen.queryAllByRole("link", { name: "Read later" })).toHaveLength(0);
  expect(screen.queryAllByRole("link", { name: "My feed" })).toHaveLength(0);
  expect(screen.getByRole("link", { name: "Sign in" })).toHaveProperty(
    "href",
    "http://localhost:3000/login?return_to=%2F",
  );
  expect(screen.queryAllByRole("link", { name: "My feed" })).toHaveLength(0);
  expect(fetcher.mock.calls.every(([url]) => !url.includes("auth/login"))).toBe(true);
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
      <TopicFollowsProvider>
        <TopicPreferences topics={[topic]} />
      </TopicFollowsProvider>
    </UserProvider>,
  );
  const tile = await screen.findByRole("button", { name: "Python" });
  fireEvent.click(tile);
  fireEvent.click(screen.getByRole("button", { name: "Save topics" }));
  await screen.findByText("Your topics are saved.");
  const write = fetcher.mock.calls.find(([, init]) => init?.method === "PUT")![1]!;
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
    vi.fn().mockResolvedValue(Response.json({ user_id: "user", csrf_token: "csrf" })),
  );
  render(
    <UserProvider>
      <UserAccount />
    </UserProvider>,
  );
  await screen.findByRole("button", { name: "User menu: Your account" });
  window.dispatchEvent(new Event("devfeed:user-session-expired"));
  await waitFor(() => expect(screen.getByRole("link", { name: "Sign in" })).toBeTruthy());
});

it("shows My feed in both navigation layouts only while signed in", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json({ user_id: "user", csrf_token: "csrf" })),
  );
  render(
    <UserProvider>
      <PersonalFeedNav />
      <PersonalFeedNav mobile />
    </UserProvider>,
  );
  expect(screen.queryAllByRole("link", { name: "My feed" })).toHaveLength(0);
  await waitFor(() => expect(screen.getAllByRole("link", { name: "My feed" })).toHaveLength(2));
  window.dispatchEvent(new Event("devfeed:user-session-expired"));
  await waitFor(() => expect(screen.queryAllByRole("link", { name: "My feed" })).toHaveLength(0));
});

it("preserves reader state across credential refreshes and clears it on account changes", async () => {
  let identity = { user_id: "reader-a", csrf_token: "session-a", name: "Reader A" };
  const fetcher = vi.fn(async (url: string) =>
    Response.json(url.endsWith("auth/me") ? identity : { display_name: null, avatar_url: null }),
  );
  vi.stubGlobal("fetch", fetcher);
  const children = (
    <>
      <UserAccount />
      <input aria-label="Reader state" defaultValue="initial" />
    </>
  );
  const view = render(<UserProvider refreshKey={0}>{children}</UserProvider>);
  await screen.findByRole("button", { name: "User menu: Reader A" });
  const input = screen.getByRole("textbox", { name: "Reader state" });
  fireEvent.change(input, { target: { value: "keep this" } });
  identity = { ...identity, csrf_token: "rotated-token" };
  view.rerender(<UserProvider refreshKey={1}>{children}</UserProvider>);
  await waitFor(() =>
    expect(fetcher.mock.calls.filter(([url]) => url.endsWith("settings/profile"))).toHaveLength(2),
  );
  expect(screen.getByRole("textbox", { name: "Reader state" })).toBe(input);
  expect(input).toHaveProperty("value", "keep this");
  identity = { user_id: "reader-b", csrf_token: "session-b", name: "Reader B" };
  view.rerender(<UserProvider refreshKey={2}>{children}</UserProvider>);
  await screen.findByRole("button", { name: "User menu: Reader B" });
  expect(screen.getByRole("textbox", { name: "Reader state" })).not.toBe(input);
  expect(screen.getByRole("textbox", { name: "Reader state" })).toHaveProperty("value", "initial");
});

it("shows both private navigation links only after sign-in", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      Promise.resolve(
        Response.json(
          url.endsWith("/me")
            ? { user_id: "user", csrf_token: "csrf", name: "Reader", expires_at: 4102444800 }
            : {},
        ),
      ),
    ),
  );
  render(
    <UserProvider>
      <PersonalFeedNav />
      <ReadLaterNav />
      <ReadLaterNav mobile />
    </UserProvider>,
  );
  await waitFor(() => expect(screen.getAllByRole("link", { name: "Read later" })).toHaveLength(2));
  expect(screen.getByRole("link", { name: "My feed" }).getAttribute("href")).toBe("/");
});

it("renews the session when returning to a tab without resetting reader state", async () => {
  let now = Date.now();
  const clock = vi.spyOn(Date, "now").mockImplementation(() => now);
  const fetcher = vi.fn(async (url: string) =>
    Response.json(
      url.endsWith("auth/me")
        ? { user_id: "reader", csrf_token: "csrf", name: "Reader" }
        : { display_name: null, avatar_url: null },
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  try {
    render(
      <UserProvider>
        <UserAccount />
        <input aria-label="Reader state" />
      </UserProvider>,
    );
    await screen.findByRole("button", { name: "User menu: Reader" });
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "preserved" } });
    now += 12 * 3600 * 1000;
    fireEvent.focus(window);
    await waitFor(() =>
      expect(fetcher.mock.calls.filter(([url]) => url.endsWith("auth/me"))).toHaveLength(2),
    );
    expect(screen.getByRole("textbox")).toHaveProperty("value", "preserved");
    fireEvent.focus(window);
    expect(fetcher.mock.calls.filter(([url]) => url.endsWith("auth/me"))).toHaveLength(2);
  } finally {
    clock.mockRestore();
  }
});

it("reauthorizes immediate history returns, retaining same-account state and hiding expired data", async () => {
  const { AccountGate } = await import("@/components/user-account");
  let identity: { user_id: string; csrf_token: string; name: string } | null = {
    user_id: "reader",
    csrf_token: "csrf",
    name: "Reader",
  };
  let resolveCheck: ((response: Response) => void) | undefined;
  let delay = false;
  const fetcher = vi.fn((url: string) => {
    if (url.endsWith("auth/me") && delay)
      return new Promise<Response>((resolve) => {
        resolveCheck = resolve;
      });
    return Promise.resolve(Response.json(url.endsWith("auth/me") ? identity : {}));
  });
  vi.stubGlobal("fetch", fetcher);
  const mounted = vi.fn();
  function ReadingState() {
    useEffect(() => {
      mounted();
    }, []);
    return <input aria-label="Private reading state" defaultValue="initial" />;
  }
  render(
    <UserProvider>
      <UserAccount />
      <PersonalFeedNav />
      <ReadLaterNav />
      <AccountGate>
        <ReadingState />
      </AccountGate>
    </UserProvider>,
  );
  const input = await screen.findByRole("textbox");
  fireEvent.change(input, { target: { value: "preserved" } });
  await screen.findByRole("button", { name: "User menu: Reader" });
  input.focus();
  delay = true;
  fireEvent(window, new PopStateEvent("popstate"));
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByRole("button", { name: "User menu: Reader" })).toBeNull();
  expect(screen.queryByRole("link", { name: "My feed" })).toBeNull();
  expect(screen.queryByRole("link", { name: "Read later" })).toBeNull();
  await act(async () => resolveCheck!(Response.json(identity)));
  expect(await screen.findByRole("textbox")).toBe(input);
  expect(input).toHaveProperty("value", "preserved");
  expect(document.activeElement).toBe(input);
  expect(mounted).toHaveBeenCalledTimes(1);
  fireEvent(window, new PageTransitionEvent("pageshow", { persisted: false }));
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith("auth/me"))).toHaveLength(2);
  fireEvent(window, new PageTransitionEvent("pagehide", { persisted: true }));
  expect(screen.queryByRole("textbox")).toBeNull();
  identity = null;
  fireEvent(window, new PageTransitionEvent("pageshow", { persisted: true }));
  await act(async () => resolveCheck!(Response.json(null)));
  expect(await screen.findAllByRole("link", { name: "Sign in" })).toHaveLength(2);
  expect(screen.queryByRole("textbox")).toBeNull();
});
