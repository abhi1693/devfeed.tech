// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { NotificationInbox } from "@/components/notification-inbox";
import { UserProvider } from "@/components/user-account";
import { notificationArticle } from "@/lib/inbox";

const mocks = vi.hoisted(() => ({
  close: vi.fn(),
  connect: vi.fn(),
  read: vi.fn(),
  seen: vi.fn(),
  all: vi.fn(),
}));
const article = "00000000-0000-0000-0000-000000000001";
vi.mock("@chimely/client", () => ({
  ChimelyClient: class {
    snapshot = {
      items: [
        {
          id: "notif_test",
          source: "notification",
          payload: {
            title: "Python update",
            body: "New in your topics",
            action_url: "/articles/00000000-0000-0000-0000-000000000001",
          },
          occurredAt: "2026-09-11T10:00:00Z",
          read: false,
        },
      ],
      counts: { unread: 1, unseen: 1 },
      status: "connected",
      error: null,
      hasMore: false,
      isLoading: false,
    };
    getSnapshot = () => this.snapshot;
    subscribe = () => () => {};
    connect = mocks.connect;
    close = mocks.close;
    markRead = mocks.read;
    markAllSeen = mocks.seen;
    markAllRead = mocks.all;
  },
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

it("does not load an inbox for anonymous visitors", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json(null));
  vi.stubGlobal("fetch", fetcher);
  render(
    <UserProvider>
      <NotificationInbox />
    </UserProvider>,
  );
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  expect(screen.queryByRole("button", { name: /Notifications/ })).toBeNull();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(mocks.connect).not.toHaveBeenCalled();
});

it("shows unread counts, opens article modal links and closes on sign out", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      Promise.resolve(
        Response.json(
          url.endsWith("auth/me")
            ? { user_id: "user-a", csrf_token: "csrf" }
            : { enabled: true, environment: "users", subscriber_id: "user_a" },
        ),
      ),
    ),
  );
  const view = render(
    <UserProvider>
      <NotificationInbox />
    </UserProvider>,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Notifications, 1 unread" }),
  );
  expect(mocks.seen).toHaveBeenCalledOnce();
  const link = screen.getByText("Python update").closest("a")!;
  expect(link.getAttribute("href")).toBe(`/articles/${article}`);
  const panel = view.container.querySelector("[popover]") as HTMLElement;
  panel.hidePopover = vi.fn();
  fireEvent.click(link);
  expect(mocks.read).toHaveBeenCalledOnce();
  expect(panel.hidePopover).toHaveBeenCalledOnce();
  fireEvent(window, new Event("devfeed:user-session-expired"));
  expect(screen.queryByRole("button", { name: /Notifications/ })).toBeNull();
  expect(mocks.close).toHaveBeenCalled();
});

it("only opens local article URLs", () => {
  expect(notificationArticle(`/articles/${article}`)).toBe(
    `/articles/${article}`,
  );
  for (const url of [
    "//evil.test",
    "javascript:alert(1)",
    "/preferences",
    "/articles/../admin",
    null,
  ])
    expect(notificationArticle(url)).toBeNull();
});
