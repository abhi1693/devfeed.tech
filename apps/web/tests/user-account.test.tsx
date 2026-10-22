// @vitest-environment jsdom
import { useEffect } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  UserProvider,
  UserAccount,
  PersonalFeedNav,
  ReadLaterNav,
  AccountGate,
  useUser,
} from "@/components/user-account";
import { TopicFollowsProvider } from "@/components/topic-follows";
import { TopicPreferences } from "@/components/topic-preferences";
import type { Topic } from "@/lib/types";
import type { UserIdentity } from "@/lib/user";
import { beginReaderNavigation } from "@/lib/reader-navigation";
import { articlePreviewOpened } from "@/lib/reader-preview-history";
import * as readerRuntime from "@/lib/reader-runtime";
afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
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
  await screen.findByRole("button", { name: "User menu: RA Reader A" });
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
  await screen.findByRole("button", { name: "User menu: RB Reader B" });
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
    await screen.findByRole("button", { name: "User menu: R Reader" });
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
  await screen.findByRole("button", { name: "User menu: R Reader" });
  input.focus();
  delay = true;
  fireEvent(window, new PopStateEvent("popstate"));
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByRole("button", { name: "User menu: R Reader" })).toBeNull();
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

const confirmedReader: UserIdentity = {
  user_id: "reader-a",
  csrf_token: "session-a",
  expires_at: 4102444800,
  name: "Reader A",
  email: "reader@example.test",
};

function previewSession() {
  let pending: Promise<Response> | null = null;
  const identities = vi.fn();
  const fetcher = vi.fn((path: string) =>
    path.endsWith("auth/me")
      ? (pending ?? Promise.resolve(Response.json(confirmedReader)))
      : path.endsWith("auth/sso/logout")
        ? Promise.resolve(Response.json({ url: "https://issuer.test/end-session?state=bound" }))
        : Promise.resolve(Response.json({ display_name: null, avatar_url: null })),
  );
  vi.stubGlobal("fetch", fetcher);
  function SessionState() {
    const { user, loading, sessionRevision, signOut } = useUser();
    useEffect(() => {
      identities(user);
    }, [user]);
    return (
      <>
        <output aria-label="Session revision">{sessionRevision}</output>
        <output aria-label="Session loading">{String(loading)}</output>
        <output aria-label="Session name">{user?.name ?? "guest"}</output>
        <output aria-label="Session email">{user?.email ?? "none"}</output>
        <button onClick={() => void signOut()}>End session</button>
      </>
    );
  }
  window.history.replaceState(null, "", "/latest?tag=rust#articles");
  render(
    <UserProvider>
      <SessionState />
      <AccountGate>
        <input aria-label="Private reading state" defaultValue="initial" />
      </AccountGate>
    </UserProvider>,
  );
  return {
    fetcher,
    identities,
    defer() {
      let complete!: (response: Response) => void;
      pending = new Promise<Response>((resolve) => {
        complete = resolve;
      });
      return async (value: UserIdentity | null, status = 200) => {
        await act(async () => {
          pending = null;
          complete(Response.json(value, { status }));
        });
      };
    },
  };
}

function openPreview(commit = true) {
  act(() => {
    beginReaderNavigation("/articles/rust-traits");
    window.history.pushState(null, "", "/articles/rust-traits");
    if (commit) window.dispatchEvent(new Event(articlePreviewOpened));
  });
}

function returnToBackground(path = "/latest?tag=rust#articles") {
  act(() => {
    window.history.replaceState(null, "", path);
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
}

it("keeps the retained feed visible and avoids session-dependent reloads when a preview closes", async () => {
  const session = previewSession();
  const input = await screen.findByRole("textbox", { name: "Private reading state" });
  await waitFor(() =>
    expect(
      session.fetcher.mock.calls.filter(([path]) => path.endsWith("settings/profile")),
    ).toHaveLength(1),
  );
  const revision = screen.getByLabelText("Session revision").textContent;
  const identity = session.identities.mock.lastCall![0];
  fireEvent.change(input, { target: { value: "retained scroll and selection" } });
  const complete = session.defer();
  openPreview();
  returnToBackground();
  expect(screen.getByRole("textbox", { name: "Private reading state" })).toBe(input);
  expect(screen.getByLabelText("Session loading").textContent).toBe("false");
  expect(session.fetcher.mock.calls.filter(([path]) => path.endsWith("auth/me"))).toHaveLength(2);
  await complete({ ...confirmedReader });
  expect(screen.getByLabelText("Session revision").textContent).toBe(revision);
  expect(session.identities.mock.lastCall![0]).toBe(identity);
  expect(session.identities.mock.calls.filter(([value]) => value !== null)).toHaveLength(1);
  expect(
    session.fetcher.mock.calls.filter(([path]) => path.endsWith("settings/profile")),
  ).toHaveLength(1);
  expect(input).toHaveProperty("value", "retained scroll and selection");

  const completeAgain = session.defer();
  openPreview();
  returnToBackground();
  expect(screen.getByRole("textbox", { name: "Private reading state" })).toBe(input);
  await completeAgain({ ...confirmedReader });
  expect(screen.getByLabelText("Session revision").textContent).toBe(revision);
  expect(
    session.fetcher.mock.calls.filter(([path]) => path.endsWith("settings/profile")),
  ).toHaveLength(1);
});

it.each(["csrf_token", "expires_at"] as const)(
  "refreshes dependent account data when preview validation changes %s",
  async (credential) => {
    const session = previewSession();
    await screen.findByRole("textbox", { name: "Private reading state" });
    const revision = Number(screen.getByLabelText("Session revision").textContent);
    const complete = session.defer();
    openPreview();
    returnToBackground();
    await complete({
      ...confirmedReader,
      ...(credential === "csrf_token"
        ? { csrf_token: "rotated-session" }
        : { expires_at: confirmedReader.expires_at + 3600 }),
    });
    expect(Number(screen.getByLabelText("Session revision").textContent)).toBeGreaterThan(revision);
    await waitFor(() =>
      expect(
        session.fetcher.mock.calls.filter(([path]) => path.endsWith("settings/profile")),
      ).toHaveLength(2),
    );
  },
);

it.each([
  ["name", "Updated reader", "Session name"],
  ["email", "updated@example.test", "Session email"],
] as const)("updates %s without invalidating the retained feed", async (field, value, label) => {
  const session = previewSession();
  const input = await screen.findByRole("textbox", { name: "Private reading state" });
  const revision = screen.getByLabelText("Session revision").textContent;
  const complete = session.defer();
  openPreview();
  returnToBackground();
  await complete({ ...confirmedReader, [field]: value });
  expect(screen.getByLabelText(label).textContent).toBe(value);
  expect(screen.getByLabelText("Session revision").textContent).toBe(revision);
  expect(screen.getByRole("textbox", { name: "Private reading state" })).toBe(input);
  expect(
    session.fetcher.mock.calls.filter(([path]) => path.endsWith("settings/profile")),
  ).toHaveLength(1);
});

it.each(["signed out", "different account", "failed check"] as const)(
  "clears the retained account after preview validation reports %s",
  async (result) => {
    const session = previewSession();
    const input = await screen.findByRole("textbox", { name: "Private reading state" });
    fireEvent.change(input, { target: { value: "private previous account state" } });
    const complete = session.defer();
    openPreview();
    returnToBackground();
    expect(screen.getByRole("textbox", { name: "Private reading state" })).toBe(input);
    await complete(
      result === "different account"
        ? { ...confirmedReader, user_id: "reader-b", csrf_token: "session-b", name: "Reader B" }
        : null,
      result === "failed check" ? 503 : 200,
    );
    expect(input.isConnected).toBe(false);
    expect(screen.queryByDisplayValue("private previous account state")).toBeNull();
    if (result === "different account") {
      expect(screen.getByLabelText("Session name").textContent).toBe("Reader B");
      expect(screen.getByRole("textbox", { name: "Private reading state" })).toHaveProperty(
        "value",
        "initial",
      );
    } else {
      expect(screen.queryByRole("textbox", { name: "Private reading state" })).toBeNull();
    }
  },
);

it("reauthorizes a frozen document even when it previously had a retained preview", async () => {
  const session = previewSession();
  await screen.findByRole("textbox", { name: "Private reading state" });
  const complete = session.defer();
  openPreview();
  fireEvent(window, new PageTransitionEvent("pagehide", { persisted: true }));
  returnToBackground();
  expect(screen.queryByRole("textbox", { name: "Private reading state" })).toBeNull();
  expect(screen.getByLabelText("Session loading").textContent).toBe("true");
  await complete({ ...confirmedReader });
  expect(await screen.findByRole("textbox", { name: "Private reading state" })).toBeTruthy();
});

it("hides the retained feed when its confirmed session has already expired", async () => {
  const session = previewSession();
  await screen.findByRole("textbox", { name: "Private reading state" });
  const complete = session.defer();
  vi.spyOn(Date, "now").mockReturnValue((confirmedReader.expires_at + 1) * 1000);
  openPreview();
  returnToBackground();
  expect(screen.queryByRole("textbox", { name: "Private reading state" })).toBeNull();
  expect(screen.getByLabelText("Session loading").textContent).toBe("true");
  await complete(null);
  expect(screen.queryByRole("textbox", { name: "Private reading state" })).toBeNull();
});

it("does not restore expired account data from a late preview validation response", async () => {
  const session = previewSession();
  const input = await screen.findByRole("textbox", { name: "Private reading state" });
  const complete = session.defer();
  openPreview();
  returnToBackground();
  fireEvent(window, new Event("devfeed:user-session-expired"));
  expect(input.isConnected).toBe(false);
  expect(screen.queryByRole("textbox", { name: "Private reading state" })).toBeNull();
  await complete({ ...confirmedReader });
  expect(screen.getByLabelText("Session name").textContent).toBe("guest");
  expect(screen.queryByRole("textbox", { name: "Private reading state" })).toBeNull();
});

it.each(["never committed", "committed a different article"])(
  "masks private state when article navigation %s",
  async (result) => {
    const session = previewSession();
    await screen.findByRole("textbox", { name: "Private reading state" });
    const complete = session.defer();
    openPreview(false);
    if (result === "committed a different article") {
      act(() => {
        window.history.replaceState(null, "", "/articles/another-article");
        window.dispatchEvent(new Event(articlePreviewOpened));
      });
    }
    returnToBackground();
    expect(screen.queryByRole("textbox", { name: "Private reading state" })).toBeNull();
    expect(screen.getByLabelText("Session loading").textContent).toBe("true");
    await complete({ ...confirmedReader });
    expect(await screen.findByRole("textbox", { name: "Private reading state" })).toBeTruthy();
  },
);

it("ignores a late preview validation response after successful sign-out", async () => {
  const browser = { complete: vi.fn(), cancel: vi.fn() };
  vi.spyOn(readerRuntime, "prepareReaderSignOut").mockReturnValue(browser);
  const session = previewSession();
  const input = await screen.findByRole("textbox", { name: "Private reading state" });
  const complete = session.defer();
  openPreview();
  returnToBackground();
  fireEvent.click(screen.getByRole("button", { name: "End session" }));
  await waitFor(() =>
    expect(browser.complete).toHaveBeenCalledWith("https://issuer.test/end-session?state=bound"),
  );
  expect(input.isConnected).toBe(false);
  await complete({ ...confirmedReader });
  expect(screen.getByLabelText("Session name").textContent).toBe("guest");
  expect(screen.queryByRole("textbox", { name: "Private reading state" })).toBeNull();
});
