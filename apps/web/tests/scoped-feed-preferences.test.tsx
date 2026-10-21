// @vitest-environment jsdom
import { useEffect } from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FeedPreferencesOverride, useFeedPreferences } from "@/components/feed-preferences";
import { ScopedFeedPreferences, useFeedViewScope } from "@/components/scoped-feed-preferences";
import { guestFeedViewsKey, type FeedViewScope } from "@/lib/feed-view-scope";
import { userRequest, type UserIdentity } from "@/lib/user";
import type { FeedViewPreference } from "@/lib/reader-layouts";
import { guestReaderPreferencesKey } from "@/lib/reader-preferences";

vi.mock("@/lib/user", () => ({ userRequest: vi.fn() }));
const session = vi.hoisted(() => ({
  user: null as UserIdentity | null,
  loading: false,
  sessionRevision: 1,
}));
vi.mock("@/components/user-account", () => ({ useUser: () => session }));
const account: UserIdentity = {
  user_id: "first",
  csrf_token: "csrf-first",
  name: "Reader",
  email: null,
  expires_at: 9999999999,
};
type Preferences = ReturnType<typeof useFeedPreferences>;
let shared: Partial<Preferences>;
let preferences: Preferences;
let scopeSeen: FeedViewScope | null;
function Probe() {
  const value = useFeedPreferences();
  const scope = useFeedViewScope();
  useEffect(() => {
    preferences = value;
    scopeSeen = scope;
  }, [value, scope]);
  return (
    <div
      data-testid="feed"
      data-view={value.view}
      data-loading={String(value.loading)}
      data-busy={String(value.busy)}
    />
  );
}
function Harness({ scope = "latest" }: { scope?: FeedViewScope }) {
  return (
    <FeedPreferencesOverride value={shared}>
      <ScopedFeedPreferences scope={scope}>
        <Probe />
      </ScopedFeedPreferences>
    </FeedPreferencesOverride>
  );
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
const ready = () => waitFor(() => expect(preferences.loading).toBe(false));
beforeEach(() => {
  localStorage.clear();
  session.user = null;
  session.loading = false;
  session.sessionRevision = 1;
  shared = {
    view: "stacked",
    storedView: "stacked",
    loading: false,
    busy: false,
    unavailable: false,
    error: "",
    languages: ["fr"],
    content_types: ["news"],
    columns: [],
    save: vi.fn().mockResolvedValue(true),
    setView: vi.fn().mockResolvedValue(true),
    refresh: vi.fn(),
  };
  vi.mocked(userRequest).mockReset();
  vi.mocked(userRequest).mockResolvedValue({ view: "compact" });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("has no scope outside the provider and never changes inherited filters or save APIs", async () => {
  const outside = render(<Probe />);
  expect(scopeSeen).toBeNull();
  outside.unmount();
  render(<Harness />);
  await ready();
  expect(scopeSeen).toBe("latest");
  expect(preferences.view).toBe("stacked");
  expect(preferences.languages).toBe(shared.languages);
  expect(preferences.columns).toBe(shared.columns);
  expect(preferences.save).toBe(shared.save);
  expect(preferences.setView).not.toBe(shared.setView);
  expect(userRequest).not.toHaveBeenCalled();
  expect(localStorage.getItem(guestFeedViewsKey)).toBeNull();
});

it("saves guest layouts separately per feed and freezes the untouched legacy default", async () => {
  const legacy = JSON.stringify({ version: 1, view: "stacked", columns: [] });
  localStorage.setItem(guestReaderPreferencesKey, legacy);
  const app = render(<Harness />);
  await ready();
  await act(async () => expect(await preferences.setView("compact")).toBe(true));
  expect(preferences.view).toBe("compact");
  expect(localStorage.getItem(guestReaderPreferencesKey)).toBe(legacy);
  app.rerender(<Harness scope="trending" />);
  await ready();
  expect(preferences.view).toBe("stacked");
  await act(async () => expect(await preferences.setView("cards")).toBe(true));
  app.rerender(<Harness />);
  await ready();
  expect(preferences.view).toBe("compact");
  expect(JSON.parse(localStorage.getItem(guestFeedViewsKey)!)).toEqual({
    version: 1,
    default_view: "stacked",
    views: { latest: "compact", trending: "cards" },
  });
  expect(shared.setView).not.toHaveBeenCalled();
  expect(shared.save).not.toHaveBeenCalled();
  expect(userRequest).not.toHaveBeenCalled();
});

it("keeps a denied guest save usable for this tab and forbids guest Columns", async () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("denied");
  });
  render(<Harness scope="my_feed" />);
  await ready();
  await act(async () => expect(await preferences.setView("columns")).toBe(false));
  expect(preferences.view).toBe("stacked");
  await act(async () => expect(await preferences.setView("compact")).toBe(false));
  expect(preferences.view).toBe("compact");
  expect(preferences.error).toContain("this browser");
  expect(preferences.busy).toBe(false);
  expect(userRequest).not.toHaveBeenCalled();
});

it("leaves a future guest storage version intact and prevents replacing it", async () => {
  const future = JSON.stringify({ version: 2, views: { latest: "future" } });
  localStorage.setItem(guestFeedViewsKey, future);
  render(<Harness />);
  await ready();
  expect(preferences.unavailable).toBe(true);
  await act(async () => expect(await preferences.setView("compact")).toBe(false));
  expect(localStorage.getItem(guestFeedViewsKey)).toBe(future);
});

it("reads and saves account scopes with CSRF without touching guest preferences", async () => {
  session.user = account;
  const getStorage = vi.spyOn(Storage.prototype, "getItem");
  const setStorage = vi.spyOn(Storage.prototype, "setItem");
  const app = render(<Harness scope="my_feed" />);
  await ready();
  expect(preferences.view).toBe("compact");
  expect(vi.mocked(userRequest).mock.calls[0][0]).toBe("settings/feed-view?feed=my_feed");
  vi.mocked(userRequest).mockResolvedValueOnce({ view: "columns" });
  await act(async () => expect(await preferences.setView("columns")).toBe(true));
  expect(preferences.view).toBe("columns");
  expect(userRequest).toHaveBeenLastCalledWith(
    "settings/feed-view?feed=my_feed",
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ view: "columns" }),
      headers: expect.objectContaining({ "X-CSRF-Token": "csrf-first" }),
    }),
  );
  app.rerender(<Harness scope="latest" />);
  await ready();
  expect(preferences.view).toBe("compact");
  const calls = vi.mocked(userRequest).mock.calls.length;
  await act(async () => expect(await preferences.setView("columns")).toBe(false));
  expect(userRequest).toHaveBeenCalledTimes(calls);
  expect(getStorage).not.toHaveBeenCalled();
  expect(setStorage).not.toHaveBeenCalled();
});

it("rolls a failed optimistic save back and blocks overlapping writes", async () => {
  session.user = account;
  render(<Harness />);
  await ready();
  const save = deferred<{ view: FeedViewPreference }>();
  vi.mocked(userRequest).mockReturnValueOnce(save.promise);
  let pending!: Promise<boolean>;
  act(() => {
    pending = preferences.setView("stacked");
  });
  expect(preferences.view).toBe("stacked");
  expect(preferences.busy).toBe(true);
  await act(async () => expect(await preferences.setView("cards")).toBe(false));
  await act(async () => {
    save.reject(new Error("unavailable"));
    expect(await pending).toBe(false);
  });
  expect(preferences.view).toBe("compact");
  expect(preferences.busy).toBe(false);
  expect(preferences.error).toContain("Couldn’t save");
  expect(userRequest).toHaveBeenCalledTimes(2);
});

it("offers a scoped retry after a failed initial read without writing a fallback", async () => {
  session.user = account;
  vi.mocked(userRequest).mockRejectedValueOnce(new Error("offline"));
  render(<Harness />);
  await ready();
  expect(preferences.view).toBe("stacked");
  expect(preferences.unavailable).toBe(true);
  await act(async () => expect(await preferences.setView("cards")).toBe(false));
  act(() => preferences.refresh());
  await waitFor(() => expect(preferences.unavailable).toBe(false));
  expect(preferences.view).toBe("compact");
  expect(userRequest).toHaveBeenCalledTimes(2);
  expect(vi.mocked(userRequest).mock.calls.every(([, init]) => !init?.method)).toBe(true);
});

it("ignores late reads from a replaced scope even if the transport ignores cancellation", async () => {
  session.user = account;
  const old = deferred<{ view: FeedViewPreference }>();
  vi.mocked(userRequest).mockReturnValueOnce(old.promise);
  const app = render(<Harness />);
  await waitFor(() => expect(userRequest).toHaveBeenCalledOnce());
  const signal = vi.mocked(userRequest).mock.calls[0][1]!.signal!;
  app.rerender(<Harness scope="trending" />);
  await ready();
  expect(signal.aborted).toBe(true);
  await act(async () => old.resolve({ view: "stacked" }));
  expect(scopeSeen).toBe("trending");
  expect(preferences.view).toBe("compact");
});

it("aborts an old account write and prevents its late receipt from changing another owner", async () => {
  session.user = account;
  const app = render(<Harness />);
  await ready();
  const save = deferred<{ view: FeedViewPreference }>();
  vi.mocked(userRequest).mockReturnValueOnce(save.promise);
  let pending!: Promise<boolean>;
  act(() => {
    pending = preferences.setView("stacked");
  });
  const signal = vi.mocked(userRequest).mock.calls[1][1]!.signal!;
  session.user = { ...account, user_id: "second", csrf_token: "csrf-second" };
  session.sessionRevision++;
  app.rerender(<Harness />);
  await ready();
  expect(signal.aborted).toBe(true);
  expect(preferences.view).toBe("compact");
  await act(async () => {
    save.resolve({ view: "stacked" });
    expect(await pending).toBe(false);
  });
  expect(preferences.view).toBe("compact");
  expect(preferences.busy).toBe(false);
  expect(preferences.error).toBe("");
});

it("retains the confirmed layout and DOM during same-owner checks without rereading on quiet preview renders", async () => {
  session.user = account;
  const app = render(<Harness scope="my_feed" />);
  await ready();
  const retained = screen.getByTestId("feed");
  session.user = { ...account, name: "Renamed" };
  app.rerender(<Harness scope="my_feed" />);
  await act(async () => {});
  expect(userRequest).toHaveBeenCalledOnce();
  expect(screen.getByTestId("feed")).toBe(retained);
  session.loading = true;
  app.rerender(<Harness scope="my_feed" />);
  expect(preferences.loading).toBe(false);
  expect(preferences.busy).toBe(true);
  expect(preferences.view).toBe("compact");
  expect(screen.getByTestId("feed")).toBe(retained);
  session.loading = false;
  session.sessionRevision++;
  app.rerender(<Harness scope="my_feed" />);
  await waitFor(() => expect(preferences.busy).toBe(false));
  expect(preferences.view).toBe("compact");
  expect(screen.getByTestId("feed")).toBe(retained);
  expect(userRequest).toHaveBeenCalledTimes(2);
});

it("aborts an unmounted read and does not let an old layout callback start a save", async () => {
  session.user = account;
  const old = deferred<{ view: FeedViewPreference }>();
  vi.mocked(userRequest).mockReturnValueOnce(old.promise);
  const app = render(<Harness />);
  await waitFor(() => expect(userRequest).toHaveBeenCalledOnce());
  const signal = vi.mocked(userRequest).mock.calls[0][1]!.signal!;
  const staleSave = preferences.setView;
  app.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => old.resolve({ view: "stacked" }));
  expect(await staleSave("cards")).toBe(false);
  expect(userRequest).toHaveBeenCalledOnce();
});

it("prevents a retained ready callback from writing after the provider unmounts", async () => {
  session.user = account;
  const app = render(<Harness />);
  await ready();
  const staleSave = preferences.setView;
  app.unmount();
  expect(await staleSave("stacked")).toBe(false);
  expect(userRequest).toHaveBeenCalledOnce();
});

it("ignores a replaced scope's late save without clearing the new scope's busy write", async () => {
  session.user = account;
  const app = render(<Harness />);
  await ready();
  const oldSave = deferred<{ view: FeedViewPreference }>();
  vi.mocked(userRequest).mockReturnValueOnce(oldSave.promise);
  let first!: Promise<boolean>;
  act(() => {
    first = preferences.setView("stacked");
  });
  app.rerender(<Harness scope="trending" />);
  await ready();
  const newSave = deferred<{ view: FeedViewPreference }>();
  vi.mocked(userRequest).mockReturnValueOnce(newSave.promise);
  let second!: Promise<boolean>;
  act(() => {
    second = preferences.setView("cards");
  });
  await act(async () => {
    oldSave.resolve({ view: "stacked" });
    expect(await first).toBe(false);
  });
  expect(preferences.view).toBe("cards");
  expect(preferences.busy).toBe(true);
  await act(async () => {
    newSave.resolve({ view: "cards" });
    expect(await second).toBe(true);
  });
  expect(preferences.busy).toBe(false);
});
