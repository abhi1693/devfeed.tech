// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MustReads } from "@/components/must-reads";
import { openMustReadsEvent } from "@/lib/reading-streak";
import { article } from "./fixtures";
const state = vi.hoisted(() => ({
  user: { user_id: "reader", csrf_token: "csrf" } as { user_id: string; csrf_token: string } | null,
  path: "/latest",
  request: vi.fn(),
}));
vi.mock("@/components/user-account", () => ({ useUser: () => state }));
vi.mock("next/navigation", () => ({ usePathname: () => state.path }));
vi.mock("@/lib/user", () => ({ userRequest: state.request }));
vi.mock("@/components/article-card", () => ({ ArticleCard: () => <article>Pick</article> }));
vi.mock("@/components/article-engagement", () => ({
  EngagementProvider: ({ children }: { children: React.ReactNode }) => children,
  bookmarkChanged: "bookmark",
}));
beforeEach(() => {
  vi.useFakeTimers();
  state.user = { user_id: "reader", csrf_token: "csrf" };
  state.path = "/latest";
  state.request.mockReset();
  state.request.mockImplementation(async (path: string) =>
    path.endsWith("presentation")
      ? { claimed: true }
      : {
          date: "2026-10-03",
          items: [article],
          reasons: {},
          read_ids: [],
          presented: false,
          preparing: false,
        },
  );
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
});
afterEach(() => {
  cleanup();
  document.querySelectorAll("[data-must-reads-blocker]").forEach((node) => node.remove());
  vi.restoreAllMocks();
  vi.useRealTimers();
});
it("defers presentation while reading an article, then claims once and permits reopening", async () => {
  state.path = "/articles/test";
  const view = render(<MustReads />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(state.request.mock.calls.some(([path]) => path.endsWith("presentation"))).toBe(false);
  state.path = "/latest";
  view.rerender(<MustReads />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(screen.getByRole("dialog", { name: "Today’s Must Reads" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Close Must Reads" }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Today’s Must Reads" }));
  expect(screen.getByRole("dialog", { name: "Today’s Must Reads" })).toBeTruthy();
  state.user = null;
  view.rerender(<MustReads />);
  expect(screen.queryByRole("dialog")).toBeNull();
});
it("keeps automatic presentation closed when another device claimed it", async () => {
  state.request.mockImplementation(async (path: string) =>
    path.endsWith("presentation")
      ? { claimed: false }
      : { date: "2026-10-03", items: [article], reasons: {}, read_ids: [], presented: false },
  );
  render(<MustReads />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(6000);
  });
  expect(screen.queryByRole("dialog")).toBeNull();
});
it.each(["article", "dialog", "menu", "hidden"])(
  "keeps an in-flight claim pending while blocked by %s, even after a selection refresh",
  async (blocker) => {
    let resolveClaim!: (value: { claimed: boolean }) => void;
    let claimed = false;
    state.request.mockImplementation((path: string) =>
      path.endsWith("presentation")
        ? new Promise((resolve) => {
            resolveClaim = resolve;
          })
        : Promise.resolve({
            date: "2026-10-03",
            items: [article],
            reasons: {},
            read_ids: [],
            presented: claimed,
            preparing: false,
          }),
    );
    const view = render(<MustReads />);
    await act(async () => {});
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    const overlay = document.createElement("div");
    overlay.setAttribute("data-must-reads-blocker", "");
    if (blocker === "article") {
      state.path = "/articles/test";
      view.rerender(<MustReads />);
    } else if (blocker === "hidden") {
      vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    } else {
      overlay.setAttribute("role", blocker);
      if (blocker === "menu") overlay.setAttribute("data-state", "open");
      document.body.append(overlay);
    }
    await act(async () => {
      claimed = true;
      resolveClaim({ claimed: true });
    });
    expect(screen.queryByRole("dialog", { name: "Today’s Must Reads" })).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    if (blocker === "hidden") vi.restoreAllMocks();
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
    if (blocker !== "hidden") {
      expect(screen.queryByRole("dialog", { name: "Today’s Must Reads" })).toBeNull();
    }
    overlay.remove();
    state.path = "/latest";
    view.rerender(<MustReads />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(screen.getByRole("dialog", { name: "Today’s Must Reads" })).toBeTruthy();
    expect(state.request.mock.calls.filter(([path]) => path.endsWith("presentation"))).toHaveLength(
      1,
    );
    fireEvent.click(screen.getByRole("button", { name: "Close Must Reads" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(screen.queryByRole("dialog", { name: "Today’s Must Reads" })).toBeNull();
  },
);
it("waits for an existing dialog to close before presenting", async () => {
  const existing = document.createElement("dialog");
  existing.setAttribute("open", "");
  document.body.append(existing);
  render(<MustReads />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(state.request.mock.calls.some(([path]) => path.endsWith("presentation"))).toBe(false);
  existing.remove();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(screen.getByRole("dialog", { name: "Today’s Must Reads" })).toBeTruthy();
});
it("never automatically presents an empty or preparing selection", async () => {
  state.request.mockResolvedValue({
    date: "2026-10-03",
    items: [],
    reasons: {},
    read_ids: [],
    presented: false,
    preparing: true,
  });
  render(<MustReads />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(6000);
  });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(state.request.mock.calls.some(([path]) => path.endsWith("presentation"))).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Today’s Must Reads" }));
  expect(screen.getByRole("status").textContent).toContain("being prepared");
});
it("refreshes the selection at the next local midnight", async () => {
  vi.setSystemTime(new Date(2026, 9, 3, 23, 59, 58));
  state.request.mockResolvedValue({
    date: "2026-10-03",
    items: [article],
    reasons: {},
    read_ids: [],
    presented: true,
    preparing: false,
  });
  render(<MustReads />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(state.request).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3100);
  });
  expect(state.request).toHaveBeenCalledTimes(2);
});

it("opens today's picks from the streak action as a manual presentation", async () => {
  state.path = "/articles/test";
  state.request.mockImplementation(async (path: string) =>
    path.endsWith("presentation")
      ? { claimed: true }
      : {
          date: "2026-10-03",
          items: [article],
          reasons: {},
          read_ids: [],
          presented: true,
          preparing: false,
        },
  );
  render(<MustReads />);
  await act(async () => {});
  expect(screen.queryByRole("dialog")).toBeNull();
  await act(async () => {
    window.dispatchEvent(new Event(openMustReadsEvent));
  });
  expect(screen.getByRole("dialog", { name: "Today’s Must Reads" })).toBeTruthy();
  const presentations = state.request.mock.calls.filter(([path]) => path.endsWith("presentation"));
  expect(presentations).toHaveLength(1);
  expect(JSON.parse(presentations[0][1].body)).toMatchObject({
    date: "2026-10-03",
    automatic: false,
  });
});

it("removes the streak action listener on sign out", async () => {
  const view = render(<MustReads />);
  await act(async () => {});
  state.user = null;
  view.rerender(<MustReads />);
  const callsBefore = state.request.mock.calls.length;
  await act(async () => {
    window.dispatchEvent(new Event(openMustReadsEvent));
  });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(state.request).toHaveBeenCalledTimes(callsBefore);
});
